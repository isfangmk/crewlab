"""自动化触发器：Cron / Webhook 触发工作流运行或助理会话。

执行分发全部进程内调用（workflow_runner.start_run / chat_service 流式消费），
调度循环由 trigger_scheduler 驱动；每次触发写 TriggerRun 留痕并写审计。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.trigger import TriggerRun, TriggerRule
from app.services.audit_service import write_audit
from app.services.auth_service import hash_token

logger = logging.getLogger(__name__)

_TRIGGER_KINDS = ("cron", "webhook")
_TARGET_KINDS = ("workflow", "agent")


class TriggerValidationError(ValueError):
    """触发器参数非法。"""


def compute_next_fire(cron_expr: str, base: datetime) -> datetime:
    """按 cron 表达式计算 base 之后的下一次触发时间（UTC）。"""

    try:
        it = croniter(cron_expr, base)
        return it.get_next(datetime)
    except (ValueError, KeyError) as exc:
        raise TriggerValidationError(f"cron 表达式非法: {cron_expr} ({exc})") from exc


def validate_rule(
    *,
    kind: str,
    cron_expr: str,
    target_kind: str,
    workflow_id: int | None,
    agent_id: str | None,
    prompt: str,
) -> None:
    """创建/更新前的参数校验；契约不符显式失败。"""

    if kind not in _TRIGGER_KINDS:
        raise TriggerValidationError(f"触发器类型非法: {kind}（可选 cron / webhook）")
    if target_kind not in _TARGET_KINDS:
        raise TriggerValidationError(f"目标类型非法: {target_kind}（可选 workflow / agent）")
    if kind == "cron":
        compute_next_fire(cron_expr, datetime.now(timezone.utc))
    if target_kind == "workflow":
        if workflow_id is None:
            raise TriggerValidationError("工作流触发器必须指定 workflow_id")
    else:
        if not agent_id:
            raise TriggerValidationError("助理触发器必须指定 agent_id")
        if prompt == "":
            raise TriggerValidationError("助理触发器必须指定触发时的用户消息 prompt")


async def create_rule(
    db: AsyncSession,
    *,
    name: str,
    kind: str,
    target_kind: str,
    cron_expr: str = "",
    workflow_id: int | None = None,
    agent_id: str | None = None,
    prompt: str = "",
    values_json: str = "{}",
    enabled: bool = True,
    max_retries: int = 0,
    created_by: int | None = None,
) -> tuple[TriggerRule, str]:
    """创建触发器；webhook 型返回一次性原文 URL 段（只出现这一次）。"""

    validate_rule(
        kind=kind,
        cron_expr=cron_expr,
        target_kind=target_kind,
        workflow_id=workflow_id,
        agent_id=agent_id,
        prompt=prompt,
    )
    raw_token = ""
    token_hash = ""
    if kind == "webhook":
        raw_token = secrets.token_urlsafe(24)
        token_hash = hash_token(raw_token)
    else:
        # 列上有唯一约束：非 webhook 也要占一个不可解析的唯一值，
        # 否则多条 cron 都会写空串而撞约束。
        token_hash = f"non-webhook:{uuid4().hex}"

    rule = TriggerRule(
        name=name,
        kind=kind,
        cron_expr=cron_expr if kind == "cron" else "",
        target_kind=target_kind,
        workflow_id=workflow_id,
        agent_id=agent_id,
        prompt=prompt,
        values_json=values_json or "{}",
        enabled=enabled,
        max_retries=max_retries,
        webhook_token_hash=token_hash,
        created_by=created_by,
        next_fire_at=(
            compute_next_fire(cron_expr, datetime.now(timezone.utc)) if kind == "cron" and enabled else None
        ),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    logger.info("已创建触发器 id=%s name=%s kind=%s target=%s", rule.id, name, kind, target_kind)
    return rule, raw_token


async def update_rule(db: AsyncSession, rule: TriggerRule, patch: dict[str, Any]) -> TriggerRule:
    """更新规则；改 cron/启用态时重算 next_fire_at。"""

    merged = {
        "name": patch.get("name", rule.name),
        "values_json": patch.get("values_json", rule.values_json),
        "cron_expr": patch.get("cron_expr", rule.cron_expr),
        "target_kind": patch.get("target_kind", rule.target_kind),
        "workflow_id": patch.get("workflow_id", rule.workflow_id),
        "agent_id": patch.get("agent_id", rule.agent_id),
        "prompt": patch.get("prompt", rule.prompt),
        "enabled": patch.get("enabled", rule.enabled),
        "max_retries": patch.get("max_retries", rule.max_retries),
    }
    validate_rule(
        kind=rule.kind,
        cron_expr=merged["cron_expr"],
        target_kind=merged["target_kind"],
        workflow_id=merged["workflow_id"],
        agent_id=merged["agent_id"],
        prompt=merged["prompt"],
    )
    for key, value in merged.items():
        setattr(rule, key, value)
    if rule.kind == "cron":
        rule.next_fire_at = (
            compute_next_fire(rule.cron_expr, datetime.now(timezone.utc)) if rule.enabled else None
        )
    await db.commit()
    await db.refresh(rule)
    return rule


async def get_rule(db: AsyncSession, trigger_id: int) -> TriggerRule:
    row = await db.get(TriggerRule, trigger_id)
    if row is None:
        raise LookupError(f"触发器不存在: {trigger_id}")
    return row


async def delete_rule(db: AsyncSession, trigger_id: int) -> None:
    rule = await get_rule(db, trigger_id)
    await db.delete(rule)
    await db.commit()
    logger.info("已删除触发器 id=%s name=%s", trigger_id, rule.name)


async def list_rules(db: AsyncSession) -> list[TriggerRule]:
    rows = await db.scalars(select(TriggerRule).order_by(TriggerRule.id.desc()))
    return list(rows.all())


async def list_runs(db: AsyncSession, trigger_id: int, limit: int = 20) -> list[TriggerRun]:
    rows = await db.scalars(
        select(TriggerRun)
        .where(TriggerRun.trigger_id == trigger_id)
        .order_by(TriggerRun.id.desc())
        .limit(limit)
    )
    return list(rows.all())


async def resolve_webhook_rule(db: AsyncSession, raw_token: str) -> TriggerRule:
    """按 token 原文哈希查 webhook 触发器；未命中/停用显式失败。"""

    token_hash = hash_token(raw_token)
    rule = await db.scalar(
        select(TriggerRule).where(TriggerRule.webhook_token_hash == token_hash)
    )
    if rule is None:
        raise LookupError("webhook token 无效")
    if not rule.enabled:
        raise PermissionError("触发器已停用")
    return rule
