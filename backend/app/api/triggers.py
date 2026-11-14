"""自动化触发器：CRUD、手动触发与公开 Webhook 入口。

Webhook 端点不挂登录依赖（豁免方式与 /chat/shares/{token} 一致），
鉴权靠路径里的 token 原文哈希比对。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth, require_not_viewer
from app.db import get_db
from app.models_orm.trigger import TriggerRule, TriggerRun
from app.services import trigger_executor, trigger_service
from app.services.audit_service import write_audit

router = APIRouter(tags=["triggers"])


def rule_to_wire(rule: TriggerRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "kind": rule.kind,
        "cron_expr": rule.cron_expr,
        "target_kind": rule.target_kind,
        "workflow_id": rule.workflow_id,
        "values_json": rule.values_json,
        "agent_id": rule.agent_id,
        "prompt": rule.prompt,
        "enabled": rule.enabled,
        "max_retries": rule.max_retries,
        # 非 webhook 的 token_hash 是唯一占位值，不能据此判断类型
        "has_webhook": rule.kind == "webhook",
        "next_fire_at": rule.next_fire_at.isoformat() if rule.next_fire_at else None,
        "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


def run_to_wire(run: TriggerRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "attempt": run.attempt,
        "error": run.error,
        "ref_id": run.ref_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


class TriggerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str
    target_kind: str
    cron_expr: str = ""
    values_json: str = "{}"
    workflow_id: int | None = None
    agent_id: str | None = None
    prompt: str = ""
    enabled: bool = True
    max_retries: int = Field(default=0, ge=0, le=5)


class TriggerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cron_expr: str | None = None
    values_json: str | None = None
    workflow_id: int | None = None
    agent_id: str | None = None
    prompt: str | None = None
    enabled: bool | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)


async def _validate_target(
    db: AsyncSession, target_kind: str, workflow_id: int | None, agent_id: str | None
) -> None:
    """目标存在性校验：工作流 / 助理必须真实存在，否则 400。"""

    from app.models_orm.workflow import Workflow
    from app.services import agent_service

    if target_kind == "workflow":
        if workflow_id is None:
            raise HTTPException(status_code=400, detail="工作流触发器必须指定 workflow_id")
        workflow = await db.get(Workflow, workflow_id)
        if workflow is None:
            raise HTTPException(status_code=400, detail=f"工作流不存在: {workflow_id}")
        # 触发器固定跑已发布快照：没发布过的流程创建时就拦住，而不是到点才发现。
        if workflow.published_definition_json == "":
            raise HTTPException(
                status_code=400,
                detail=f"工作流尚未发布，不能作为触发器目标: {workflow.name}",
            )
    else:
        try:
            await agent_service.get_agent(db, agent_id or "")
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/triggers")
async def list_triggers(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    rules = await trigger_service.list_rules(db)
    return {"items": [rule_to_wire(r) for r in rules], "total": len(rules)}


@router.post("/triggers")
async def create_trigger(
    body: TriggerCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    await _validate_target(db, body.target_kind, body.workflow_id, body.agent_id)
    try:
        rule, raw_token = await trigger_service.create_rule(
            db,
            name=body.name,
            kind=body.kind,
            target_kind=body.target_kind,
            cron_expr=body.cron_expr,
            values_json=body.values_json,
            workflow_id=body.workflow_id,
            agent_id=body.agent_id,
            prompt=body.prompt,
            enabled=body.enabled,
            max_retries=body.max_retries,
            created_by=user.id,
        )
    except trigger_service.TriggerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="trigger.create",
        resource_type="trigger",
        resource_id=str(rule.id),
        detail={"name": rule.name, "kind": rule.kind, "target_kind": rule.target_kind},
    )
    wire = rule_to_wire(rule)
    if raw_token:
        wire["webhook_url"] = f"/api/v1/triggers/webhook/{raw_token}/fire"
    return wire


@router.put("/triggers/{trigger_id}")
async def update_trigger(
    trigger_id: int,
    body: TriggerUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    try:
        rule = await trigger_service.get_rule(db, trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await _validate_target(
        db,
        rule.target_kind,
        patch.get("workflow_id", rule.workflow_id),
        patch.get("agent_id", rule.agent_id),
    )
    try:
        rule = await trigger_service.update_rule(db, rule, patch)
    except trigger_service.TriggerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="trigger.update",
        resource_type="trigger",
        resource_id=str(rule.id),
        detail={"name": rule.name, "enabled": rule.enabled},
    )
    return rule_to_wire(rule)


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: int,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_admin),
) -> dict:
    try:
        rule = await trigger_service.get_rule(db, trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await trigger_service.delete_rule(db, trigger_id)
    await write_audit(
        actor_user_id=user.id,
        action="trigger.delete",
        resource_type="trigger",
        resource_id=str(trigger_id),
        detail={"name": rule.name},
    )
    return {"ok": True}


@router.get("/triggers/{trigger_id}")
async def get_trigger(
    trigger_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        rule = await trigger_service.get_rule(db, trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return rule_to_wire(rule)


@router.get("/triggers/{trigger_id}/runs")
async def list_trigger_runs(
    trigger_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await trigger_service.get_rule(db, trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = await trigger_service.list_runs(db, trigger_id)
    return {"items": [run_to_wire(r) for r in rows], "total": len(rows)}


@router.post("/triggers/{trigger_id}/fire")
async def fire_trigger(
    trigger_id: int,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    """手动触发一次（测试与验收用），走与定时/Webhook 相同的执行分发。"""

    try:
        rule = await trigger_service.get_rule(db, trigger_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # fire_rule 内部统一写 trigger.fire 审计，端点不再重复记一条。
    run = await trigger_executor.fire_rule(db, rule, actor_user_id=user.id)
    return run_to_wire(run)


@router.post("/triggers/webhook/{raw_token}/fire")
async def fire_webhook(
    raw_token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """公开 Webhook 入口：路径 token 即鉴权（豁免与只读分享一致）。"""

    try:
        rule = await trigger_service.resolve_webhook_rule(db, raw_token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    run = await trigger_executor.fire_rule(db, rule)
    return run_to_wire(run)
