"""并行排查 API：列表、快照、事件流、停止与重试。

事件流照工作流运行的 SSE 口径：连上先补发缓冲，再持续推后续事件，
右栏工作台断线重连不丢步骤；停止/重试写审计。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sse_starlette import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_auth, require_not_viewer
from app.models.investigation import InvestigationDispatch
from app.models.user import User
from app.services.investigation import dispatch_service
from app.services.investigation.investigation_events import (
    TERMINAL_EVENTS,
    investigation_events,
)
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["investigations"])


def _require_owner_or_admin(user: AuthUser, created_by: int | None) -> None:
    """非 admin 只能操作自己创建的排查；空创建人仅 admin（历史行口径）。"""

    if user.role == "admin":
        return
    if created_by is None or created_by != user.id:
        raise HTTPException(status_code=403, detail="无权操作该排查")


async def _get_dispatch_row(db: AsyncSession, dispatch_uid: str) -> InvestigationDispatch:
    row = (
        await db.scalars(
            select(InvestigationDispatch).where(
                InvestigationDispatch.dispatch_uid == dispatch_uid
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"排查不存在: {dispatch_uid}")
    return row


@router.get("/investigations")
async def list_investigations(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """排查列表（按创建时间倒序，可按会话过滤）。"""

    items, total = await dispatch_service.list_dispatches(
        db, session_id=session_id or None, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/investigations/{dispatch_uid}")
async def get_investigation(
    dispatch_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """一次排查的快照：worker 状态、全局编号发现与预算。"""

    wire = await dispatch_service.get_dispatch_wire(db, dispatch_uid)
    if wire is None:
        raise HTTPException(status_code=404, detail=f"排查不存在: {dispatch_uid}")
    return wire


@router.get("/investigations/{dispatch_uid}/events")
async def stream_dispatch_events(
    dispatch_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> EventSourceResponse:
    """订阅一次排查的事件流（SSE）。

    连上先补发已经发生的事件，再持续推后续事件；排查结束后正常收尾。
    软截止后后台续跑的事件也走这条流。
    """

    await _get_dispatch_row(db, dispatch_uid)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        # 先取缓冲再挂队列：subscribe 内部保证两者之间不漏事件
        backlog, queue = await investigation_events.subscribe(dispatch_uid)
        try:
            for item in backlog:
                yield _sse_frame(item)
            if investigation_events.is_finished(dispatch_uid):
                return
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse_frame(item)
                if item.get("event") in TERMINAL_EVENTS:
                    break
        finally:
            investigation_events.unsubscribe(dispatch_uid, queue)

    # 与聊天 / 工作流流共用同一套 SSE 响应：心跳、断连检测由框架负责
    return EventSourceResponse(event_source(), sep="\n")


def _sse_frame(item: dict[str, Any]) -> dict[str, str]:
    """SSE 帧：event 名与数据分离，前端可直接按 event 分发。"""

    return {
        "event": item.get("event", "message"),
        "data": json.dumps(item, ensure_ascii=False, default=str),
    }


@router.post("/investigations/{dispatch_uid}/stop")
async def stop_investigation(
    dispatch_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """全部停止：置协作停止标记，跑着的 worker 在事件间隙退出。"""

    row = await _get_dispatch_row(db, dispatch_uid)
    _require_owner_or_admin(user, row.created_by)
    any_active = await dispatch_service.stop_dispatch(db, dispatch_uid)
    if any_active:
        await write_audit(
            actor_user_id=user.id,
            action="investigation.stop",
            resource_type="investigation_dispatch",
            resource_id=dispatch_uid,
        )
    return {"stopped": any_active}


@router.post("/investigations/{dispatch_uid}/workers/{worker_uid}/retry")
async def retry_investigation_worker(
    dispatch_uid: str,
    worker_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """重试失败/停止的一路 worker：attempt+1 原地重跑，发现重新编号。"""

    row = await _get_dispatch_row(db, dispatch_uid)
    _require_owner_or_admin(user, row.created_by)
    try:
        worker = await dispatch_service.retry_worker(db, dispatch_uid, worker_uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if worker is None:
        raise HTTPException(status_code=404, detail=f"worker 不存在: {worker_uid}")
    await write_audit(
        actor_user_id=user.id,
        action="investigation.retry",
        resource_type="investigation_dispatch",
        resource_id=dispatch_uid,
        detail={"worker_uid": worker_uid},
    )
    return {"worker_uid": worker.worker_uid, "status": worker.status, "attempt": worker.attempt}
