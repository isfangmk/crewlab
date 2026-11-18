"""任务图 API：列表、快照、SSE 事件流、编辑与执行控制。

事件流口径：连上先补发缓冲，再持续推后续事件；右栏工作台断线重连不丢节点。
确认/停止/重试/跳过写审计。
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
from app.models.task_graph import TaskGraphRun
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser
from app.services.taskgraph import task_graph_service
from app.services.taskgraph.task_graph_events import task_graph_events

router = APIRouter(tags=["task graphs"])


class GraphNodesIn(BaseModel):
    """整体替换节点的请求体。"""

    nodes_json: str
    edges_json: str = "[]"


TERMINAL_EVENTS = {"graph_finished"}


def _require_owner_or_admin(user: AuthUser, created_by: int | None) -> None:
    if user.role == "admin":
        return
    if created_by is None or created_by != user.id:
        raise HTTPException(status_code=403, detail="无权操作该任务图")


async def _get_graph_row(db: AsyncSession, graph_uid: str) -> TaskGraphRun:
    row = (
        await db.scalars(select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务图不存在: {graph_uid}")
    return row


def _sse_frame(item: dict[str, Any]) -> dict[str, str]:
    return {
        "event": item.get("event", "message"),
        "data": json.dumps(item, ensure_ascii=False, default=str),
    }


@router.get("/task-graphs")
async def list_task_graphs(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    items, total = await task_graph_service.list_graphs(
        db, session_id=session_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/task-graphs/{graph_uid}")
async def get_task_graph(
    graph_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    wire = await task_graph_service.get_graph_wire(db, graph_uid)
    if wire is None:
        raise HTTPException(status_code=404, detail=f"任务图不存在: {graph_uid}")
    return wire


@router.get("/task-graphs/{graph_uid}/events")
async def stream_graph_events(
    graph_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> EventSourceResponse:
    await _get_graph_row(db, graph_uid)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        backlog, queue = await task_graph_events.subscribe(graph_uid)
        for item in backlog:
            yield _sse_frame(item)
        if task_graph_events.is_finished(graph_uid):
            return
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse_frame(item)
                if item.get("event") in TERMINAL_EVENTS:
                    break
        finally:
            task_graph_events.unsubscribe(graph_uid, queue)

    return EventSourceResponse(event_source(), sep="\n")


@router.patch("/task-graphs/{graph_uid}/nodes")
async def update_graph_nodes(
    graph_uid: str,
    body: GraphNodesIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    row = await _get_graph_row(db, graph_uid)
    _require_owner_or_admin(user, row.created_by)

    from app.tools.task_graph import update_task_graph

    result = await update_task_graph(graph_uid, body.nodes_json, body.edges_json)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    await write_audit(
        actor_user_id=user.id,
        action="task_graph.edit",
        resource_type="task_graph_run",
        resource_id=graph_uid,
    )
    return result


@router.post("/task-graphs/{graph_uid}/confirm")
async def confirm_graph(
    graph_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    row = await _get_graph_row(db, graph_uid)
    _require_owner_or_admin(user, row.created_by)
    ok = await task_graph_service.confirm_and_run(db, graph_uid)
    if not ok:
        raise HTTPException(status_code=409, detail="任务图不存在或已开始执行")
    await write_audit(
        actor_user_id=user.id,
        action="task_graph.start",
        resource_type="task_graph_run",
        resource_id=graph_uid,
    )
    return {"started": True}


@router.post("/task-graphs/{graph_uid}/stop")
async def stop_graph(
    graph_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    row = await _get_graph_row(db, graph_uid)
    _require_owner_or_admin(user, row.created_by)
    stopped = await task_graph_service.stop_graph(db, graph_uid)
    if stopped:
        await write_audit(
            actor_user_id=user.id,
            action="task_graph.stop",
            resource_type="task_graph_run",
            resource_id=graph_uid,
        )
    return {"stopped": stopped}


@router.post("/task-graphs/{graph_uid}/nodes/{node_uid}/retry")
async def retry_graph_node(
    graph_uid: str,
    node_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    row = await _get_graph_row(db, graph_uid)
    _require_owner_or_admin(user, row.created_by)
    node = await task_graph_service.retry_node(db, graph_uid, node_uid)
    if node is None:
        raise HTTPException(status_code=409, detail="节点不存在或不可重试")
    await write_audit(
        actor_user_id=user.id,
        action="task_graph.retry",
        resource_type="task_graph_run",
        resource_id=graph_uid,
        detail={"node_uid": node_uid},
    )
    return {"node_uid": node.node_uid, "status": node.status, "attempt": node.attempt}


@router.post("/task-graphs/{graph_uid}/nodes/{node_uid}/skip")
async def skip_graph_node(
    graph_uid: str,
    node_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    row = await _get_graph_row(db, graph_uid)
    _require_owner_or_admin(user, row.created_by)
    node = await task_graph_service.skip_node(db, graph_uid, node_uid)
    if node is None:
        raise HTTPException(status_code=409, detail="节点不存在或不可跳过")
    await write_audit(
        actor_user_id=user.id,
        action="task_graph.skip",
        resource_type="task_graph_run",
        resource_id=graph_uid,
        detail={"node_uid": node_uid},
    )
    return {"node_uid": node.node_uid, "status": node.status}
