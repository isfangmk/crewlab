"""规划执行 API：列表、快照、事件流、编辑与执行控制。

事件流照排查的 SSE 口径：连上先补发缓冲，再持续推后续事件，
右栏执行进度断线重连不丢步骤；开始/停止/重试/跳过写审计。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from app.api.deps import get_db, require_auth, require_not_viewer
from app.models.plan import PlanExecution
from app.services.investigation.investigation_events import investigation_events
from app.services.observability.audit_service import write_audit
from app.services.plan import plan_service
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["plans"])


class PlanStepsIn(BaseModel):
    """整体替换步骤的请求体。"""

    steps_json: str


def _require_owner_or_admin(user: AuthUser, created_by: int | None) -> None:
    """非 admin 只能操作自己创建的计划；空创建人仅 admin（历史行口径）。"""

    if user.role == "admin":
        return
    if created_by is None or created_by != user.id:
        raise HTTPException(status_code=403, detail="无权操作该计划")


async def _get_plan_row(db: AsyncSession, plan_uid: str) -> PlanExecution:
    row = (
        await db.scalars(select(PlanExecution).where(PlanExecution.plan_uid == plan_uid))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"计划不存在: {plan_uid}")
    return row


@router.get("/plans")
async def list_plans(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """计划列表（按创建时间倒序，可按会话过滤）。"""

    items, total = await plan_service.list_plans(
        db, session_id=session_id or None, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/plans/{plan_uid}")
async def get_plan(
    plan_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """一次计划的快照：步骤状态、结论与用量。"""

    wire = await plan_service.get_plan_wire(db, plan_uid)
    if wire is None:
        raise HTTPException(status_code=404, detail=f"计划不存在: {plan_uid}")
    return wire


@router.get("/plans/{plan_uid}/events")
async def stream_plan_events(
    plan_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> EventSourceResponse:
    """订阅一次计划执行的事件流（SSE）。

    连上先补发已经发生的事件，再持续推后续事件；计划终态后正常收尾。
    暂停/重试/跳过后的续跑事件也走这条流。
    """

    await _get_plan_row(db, plan_uid)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        # 先取缓冲再挂队列：subscribe 内部保证两者之间不漏事件
        backlog, queue = await investigation_events.subscribe(plan_uid)
        try:
            for item in backlog:
                yield _sse_frame(item)
            if investigation_events.is_finished(plan_uid):
                return
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse_frame(item)
                if item.get("event") in plan_service.PLAN_TERMINAL_EVENTS:
                    break
        finally:
            investigation_events.unsubscribe(plan_uid, queue)

    # 与聊天 / 排查流共用同一套 SSE 响应：心跳、断连检测由框架负责
    return EventSourceResponse(event_source(), sep="\n")


def _sse_frame(item: dict[str, Any]) -> dict[str, str]:
    """SSE 帧：event 名与数据分离，前端可直接按 event 分发。"""

    return {
        "event": item.get("event", "message"),
        "data": json.dumps(item, ensure_ascii=False, default=str),
    }


@router.patch("/plans/{plan_uid}/steps")
async def update_plan_steps(
    plan_uid: str,
    body: PlanStepsIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """整体替换步骤（排序/增删/改文案）；仅待确认状态可改。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    try:
        steps = plan_service.parse_steps_json(body.steps_json)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok = await plan_service.update_steps(db, plan_uid, steps)
    if not ok:
        raise HTTPException(status_code=409, detail="计划不存在或已开始执行，无法调整步骤")
    await write_audit(
        actor_user_id=user.id,
        action="plan.edit",
        resource_type="plan_execution",
        resource_id=plan_uid,
        detail={"steps": [s["name"] for s in steps]},
    )
    return await plan_service.get_plan_wire(db, plan_uid) or {}


@router.post("/plans/{plan_uid}/start")
async def start_plan(
    plan_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """确认执行 / 继续执行：起顺序执行循环。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await plan_service.start_plan(db, plan_uid)
    if not ok:
        raise HTTPException(status_code=409, detail="计划状态不允许开始执行")
    await write_audit(
        actor_user_id=user.id,
        action="plan.start",
        resource_type="plan_execution",
        resource_id=plan_uid,
    )
    return {"started": True}


@router.post("/plans/{plan_uid}/pause")
async def pause_plan(
    plan_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """暂停：当前步骤协作停止，计划转待决状态。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await plan_service.pause_plan(plan_uid)
    if ok:
        await write_audit(
            actor_user_id=user.id,
            action="plan.pause",
            resource_type="plan_execution",
            resource_id=plan_uid,
        )
    return {"paused": ok}


@router.post("/plans/{plan_uid}/stop")
async def stop_plan(
    plan_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """全部停止：当前与未执行步骤全部转停止。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await plan_service.stop_plan(db, plan_uid)
    if ok:
        await write_audit(
            actor_user_id=user.id,
            action="plan.stop",
            resource_type="plan_execution",
            resource_id=plan_uid,
        )
    return {"stopped": ok}


@router.post("/plans/{plan_uid}/steps/{step_uid}/retry")
async def retry_plan_step(
    plan_uid: str,
    step_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """重试失败/停止的一步：attempt+1 原地重跑，恢复执行循环。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await plan_service.retry_step(db, plan_uid, step_uid)
    if not ok:
        raise HTTPException(status_code=409, detail="该步骤当前状态不可重试")
    await write_audit(
        actor_user_id=user.id,
        action="plan.retry",
        resource_type="plan_execution",
        resource_id=plan_uid,
        detail={"step_uid": step_uid},
    )
    return {"retried": True}


@router.post("/plans/{plan_uid}/steps/{step_uid}/skip")
async def skip_plan_step(
    plan_uid: str,
    step_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """跳过失败/停止的一步：占位结论并继续，汇总时提示缺口。"""

    row = await _get_plan_row(db, plan_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await plan_service.skip_step(db, plan_uid, step_uid)
    if not ok:
        raise HTTPException(status_code=409, detail="该步骤当前状态不可跳过")
    await write_audit(
        actor_user_id=user.id,
        action="plan.skip",
        resource_type="plan_execution",
        resource_id=plan_uid,
        detail={"step_uid": step_uid},
    )
    return {"skipped": True}
