"""审批规则 CRUD：工具×助理×风险 的自动放行/需审批/禁用/升级策略。

列表对所有登录用户可读（对话里的工具卡要知道适用什么规则）；
增删改收口 admin，每次变更进审计。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.services.chat import approval_rule_service
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["approval-rules"])


class ApprovalRuleIn(BaseModel):
    """审批规则的新建/更新请求体（全量替换语义）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, description="规则名称，1~128 字")
    # 工具名通配（fnmatch）：delete_memory / mcp__obs-tools__* / *
    tool_pattern: str = Field(min_length=1, max_length=256, description="工具名匹配模式（fnmatch 通配），如 delete_memory、mcp__obs-tools__*、*；1~256 字")
    # 空 = 全部助理（agent name）
    agent_id: str | None = Field(default=None, description="限定生效的助理 id（agent name）；留空表示全部助理")
    risk: Literal["safe", "mutating", "dangerous"] | None = Field(default=None, description="限定生效的风险等级：safe 安全 / mutating 有副作用 / dangerous 危险；留空表示不限")
    action: Literal["auto_approve", "require_approval", "block", "escalate"] = Field(description="匹配后的处置：auto_approve 自动放行 / require_approval 需审批 / block 禁用 / escalate 升级")
    priority: int = Field(default=0, description="规则优先级，数值越大越优先匹配")
    enabled: bool = Field(default=True, description="是否启用；停用后该规则不参与匹配")


def _rule_to_wire(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "tool_pattern": row.tool_pattern,
        "agent_id": row.agent_id,
        "risk": row.risk,
        "action": row.action,
        "priority": row.priority,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/approval-rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出全部审批规则，供管理页与对话工具卡查询适用规则。"""
    rules = await approval_rule_service.list_rules(db)
    return {"items": [_rule_to_wire(r) for r in rules], "total": len(rules)}


@router.post("/approval-rules")
async def create_rule(
    body: ApprovalRuleIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员新建一条审批规则。"""
    try:
        row = await approval_rule_service.create_rule(
            db,
            name=body.name,
            tool_pattern=body.tool_pattern,
            action=body.action,
            agent_id=body.agent_id,
            risk=body.risk,
            priority=body.priority,
            enabled=body.enabled,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval_rule.create",
        resource_type="approval_rule",
        resource_id=row.id,
        detail={"action": row.action, "tool_pattern": row.tool_pattern, "agent_id": row.agent_id},
    )
    return _rule_to_wire(row)


@router.patch("/approval-rules/{rule_id}")
async def update_rule(
    rule_id: str = Path(description="要更新的规则 id"),
    body: ApprovalRuleIn = Body(description="整份规则配置（全量替换）"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员全量更新一条审批规则（提交整份配置，避免清空字段歧义）。"""
    # 全量替换语义：前端编辑表单提交整份配置，避免「清空字段」歧义
    try:
        row = await approval_rule_service.update_rule(
            db,
            rule_id,
            name=body.name,
            tool_pattern=body.tool_pattern,
            action=body.action,
            agent_id=body.agent_id,
            risk=body.risk,
            priority=body.priority,
            enabled=body.enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval_rule.update",
        resource_type="approval_rule",
        resource_id=row.id,
        detail={"action": row.action, "tool_pattern": row.tool_pattern, "enabled": row.enabled},
    )
    return _rule_to_wire(row)


@router.delete("/approval-rules/{rule_id}")
async def delete_rule(
    rule_id: str = Path(description="要删除的规则 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员删除一条审批规则。"""
    try:
        await approval_rule_service.delete_rule(db, rule_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval_rule.delete",
        resource_type="approval_rule",
        resource_id=rule_id,
        detail={},
    )
    return {"ok": True}
