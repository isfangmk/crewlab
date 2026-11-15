"""审批规则解析：工具×助理×风险 首条命中生效，匹配顺序确定可测。

规则优先于 per-server 三档策略与会话信任：命中 block/require_approval/escalate
的工具不被会话信任豁免；auto_approve 直接免审。未命中规则的工具维持现状语义。
"""

from __future__ import annotations

import fnmatch
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval_rule import ApprovalRule

logger = logging.getLogger(__name__)

RULE_ACTIONS = ("auto_approve", "require_approval", "block", "escalate")
RULE_RISKS = ("safe", "approval_required")

_MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RuleDecision:
    """一条规则命中结果：动作 + 出处（审计与排查用）。"""

    action: str
    rule_id: str
    rule_name: str


def _specificity(tool_pattern: str) -> int:
    """匹配面越窄越优先：无通配 > 前缀通配 > 其他通配（纯 * 是兜底）。"""

    has_wild = any(ch in tool_pattern for ch in "*?[")
    if not has_wild:
        return 2
    if (
        tool_pattern.endswith("*")
        and tool_pattern.count("*") == 1
        and not any(ch in tool_pattern for ch in "?[")
        # 纯 `*` 没有前缀约束，是兜底通配，不能算前缀通配
        and len(tool_pattern) > 1
    ):
        return 1
    return 0


def pick_rule(
    rules: list[ApprovalRule],
    *,
    tool_name: str,
    agent_id: str | None,
    risk: str | None,
) -> RuleDecision | None:
    """纯匹配函数：enabled 规则按 具体度 > 助理专属 > priority > created_at 取首条。"""

    matched: list[ApprovalRule] = []
    for rule in rules:
        if not rule.enabled:
            continue
        if not fnmatch.fnmatchcase(tool_name, rule.tool_pattern):
            continue
        if rule.agent_id is not None and rule.agent_id != agent_id:
            continue
        if rule.risk is not None and rule.risk != risk:
            continue
        matched.append(rule)
    if not matched:
        return None
    matched.sort(
        key=lambda r: (
            -_specificity(r.tool_pattern),
            -(r.agent_id is not None),
            -r.priority,
            r.created_at or _MIN_TS,
            r.id,
        )
    )
    winner = matched[0]
    return RuleDecision(action=winner.action, rule_id=winner.id, rule_name=winner.name)


def validate_rule_fields(
    *,
    name: str,
    tool_pattern: str,
    risk: str | None,
    action: str,
) -> None:
    """规则字段校验：非法取值显式失败。"""

    if name.strip() == "":
        raise ValueError("name 不能为空")
    if tool_pattern.strip() == "":
        raise ValueError("tool_pattern 不能为空")
    if risk is not None and risk not in RULE_RISKS:
        raise ValueError(f"risk 取值非法: {risk}")
    if action not in RULE_ACTIONS:
        raise ValueError(f"action 取值非法: {action}")


async def list_rules(db: AsyncSession) -> list[ApprovalRule]:
    """全部规则（含停用），按匹配优先级同序返回，前端表格即判定顺序。"""

    rows = (
        await db.scalars(select(ApprovalRule).order_by(ApprovalRule.created_at.asc(), ApprovalRule.id.asc()))
    ).all()
    return sorted(
        rows,
        key=lambda r: (
            -_specificity(r.tool_pattern),
            -(r.agent_id is not None),
            -r.priority,
            r.created_at or _MIN_TS,
            r.id,
        ),
    )


async def create_rule(
    db: AsyncSession,
    *,
    name: str,
    tool_pattern: str,
    action: str,
    agent_id: str | None = None,
    risk: str | None = None,
    priority: int = 0,
    enabled: bool = True,
) -> ApprovalRule:
    validate_rule_fields(name=name, tool_pattern=tool_pattern, risk=risk, action=action)
    row = ApprovalRule(
        id=f"rule_{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        enabled=enabled,
        tool_pattern=tool_pattern.strip(),
        agent_id=agent_id,
        risk=risk,
        action=action,
        priority=priority,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("创建审批规则 id=%s action=%s pattern=%s", row.id, action, tool_pattern)
    return row


async def update_rule(db: AsyncSession, rule_id: str, **fields) -> ApprovalRule:
    row = await db.get(ApprovalRule, rule_id)
    if row is None:
        raise LookupError(f"审批规则不存在: {rule_id}")
    name = fields.get("name", row.name)
    tool_pattern = fields.get("tool_pattern", row.tool_pattern)
    risk = fields.get("risk", row.risk)
    action = fields.get("action", row.action)
    validate_rule_fields(name=name, tool_pattern=tool_pattern, risk=risk, action=action)
    row.name = name.strip()
    row.tool_pattern = tool_pattern.strip()
    row.agent_id = fields.get("agent_id", row.agent_id)
    row.risk = risk
    row.action = action
    row.priority = int(fields.get("priority", row.priority))
    row.enabled = bool(fields.get("enabled", row.enabled))
    await db.commit()
    await db.refresh(row)
    logger.info("更新审批规则 id=%s action=%s", row.id, row.action)
    return row


async def delete_rule(db: AsyncSession, rule_id: str) -> None:
    row = await db.get(ApprovalRule, rule_id)
    if row is None:
        raise LookupError(f"审批规则不存在: {rule_id}")
    await db.delete(row)
    await db.commit()
    logger.info("删除审批规则 id=%s", rule_id)


async def resolve_actions(
    db: AsyncSession,
    *,
    agent_id: str | None,
    tools: dict[str, str | None],
) -> dict[str, RuleDecision]:
    """解析一组工具（wire 名 → 风险）的规则动作；未命中不出现在结果里。"""

    rules = list((await db.scalars(select(ApprovalRule))).all())
    result: dict[str, RuleDecision] = {}
    for tool_name, risk in tools.items():
        decision = pick_rule(rules, tool_name=tool_name, agent_id=agent_id, risk=risk)
        if decision is not None:
            result[tool_name] = decision
    return result


async def tool_risk(db: AsyncSession, tool_name: str) -> str | None:
    """查单个工具的当前风险标签（内置规格 / MCP 展示口径）；查不到返回 None。"""

    from app.tools.catalog import BUILTIN_TOOL_SPECS

    for spec in BUILTIN_TOOL_SPECS:
        if spec["name"] == tool_name:
            return spec["risk"]

    if tool_name.startswith("mcp__"):
        from app.services.mcp import mcp_service

        for server in await mcp_service.list_enabled_servers(db):
            # tool_risk_map 的键是原始工具名，闸门用 wire 名——换算后匹配
            for raw_name, risk in mcp_service.tool_risk_map(server).items():
                if mcp_service.mcp_tool_wire_name(server.name, raw_name) == tool_name:
                    return risk
    return None


async def resolve_action_for_tool(
    db: AsyncSession,
    *,
    agent_id: str | None,
    tool_name: str,
) -> RuleDecision | None:
    """单工具重查规则（审批建单标记 escalate 用），与装配时同一套匹配。"""

    risk = await tool_risk(db, tool_name)
    return pick_rule(
        list((await db.scalars(select(ApprovalRule))).all()),
        tool_name=tool_name,
        agent_id=agent_id,
        risk=risk,
    )


def apply_rule_actions(
    confirm_tool_names: set[str],
    disallowed_tool_names: set[str],
    rule_actions: dict[str, str],
) -> tuple[set[str], set[str]]:
    """把规则动作改写进审批/禁用名单；返回新集合，不改入参。

    auto_approve 强出审批名单；require_approval/escalate 强进；
    block 进禁用名单（闸门按 block 直接拒绝，不进审批流程）。
    """

    confirm = set(confirm_tool_names)
    disallowed = set(disallowed_tool_names)
    for tool_name, action in rule_actions.items():
        if action == "block":
            disallowed.add(tool_name)
            confirm.discard(tool_name)
        elif action in ("require_approval", "escalate"):
            confirm.add(tool_name)
            disallowed.discard(tool_name)
        elif action == "auto_approve":
            confirm.discard(tool_name)
    return confirm, disallowed
