"""任务图 API：列表、快照、SSE 事件流、编辑与执行控制。

事件流口径：连上先补发缓冲，再持续推后续事件；右栏工作台断线重连不丢节点。
确认/停止/重试/跳过写审计。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from app.adk_runtime.headless import WorkerBudget
from app.api.deps import get_db, require_auth, require_not_viewer
from app.models.chat import ChatMessage, ChatSession
from app.models.task_graph import TaskGraphRun
from app.schemas.task_graph import TaskGraphNodeSpec, TaskGraphSpec
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser
from app.services.taskgraph import task_graph_service
from app.services.taskgraph.task_graph_events import task_graph_events

router = APIRouter(tags=["task graphs"])
logger = logging.getLogger(__name__)


class GraphNodesIn(BaseModel):
    """整体替换节点的请求体。"""

    nodes_json: str
    edges_json: str = "[]"


class GraphFromMessageIn(BaseModel):
    """从一条助理回答创建单节点任务图草稿。"""

    message_id: str
    title: str = ""


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


@router.post("/task-graphs/from-message")
async def create_graph_from_message(
    body: GraphFromMessageIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """从一条助理回答创建单节点任务图草稿（不执行）。

    把回答正文作为节点的 target/instruction 提示语，用户在工作台确认后再执行；
    复用 submit_graph 落库（draft 状态），归属与审计与手工建图一致。
    """
    msg = await db.get(ChatMessage, body.message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail=f"消息不存在: {body.message_id}")
    if msg.role != "assistant" or msg.kind not in (None, "message"):
        raise HTTPException(status_code=400, detail="只能从助理回答正文创建任务图")
    session = await db.get(ChatSession, msg.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {msg.session_id}")
    # 会话属主校验：防止拿别人的消息建图。
    from app.api.chat import _require_session_owner

    _require_session_owner(session, user)

    answer = (msg.content or "").strip()
    if answer == "":
        raise HTTPException(status_code=400, detail="消息正文为空，无法生成任务图")
    title = (body.title or "").strip()[:256] or "从回答创建的任务"
    # 单节点：把回答要点作为排查/执行目标，边界写清"按此回答执行"。
    spec = TaskGraphSpec(
        title=title,
        question=answer[:8000],
        nodes=[
            TaskGraphNodeSpec(
                id="n1",
                name=title[:128],
                target=answer[:2000],
                instruction=f"按以下回答内容执行并给出结果：\n{answer[:6000]}",
                tools=["__none__"],
            )
        ],
    )
    graph = await task_graph_service.submit_graph(
        db,
        session_id=msg.session_id,
        spec=spec,
        budget=WorkerBudget(),
        card_message_id=msg.id,
        created_by=user.id,
    )
    await write_audit(
        actor_user_id=user.id,
        action="task_graph.create_from_message",
        resource_type="task_graph_run",
        resource_id=graph.graph_uid,
        detail={"message_id": msg.id, "session_id": msg.session_id},
    )
    logger.info(
        "从消息建任务图 graph_uid=%s message_id=%s session_id=%s",
        graph.graph_uid,
        msg.id,
        msg.session_id,
    )
    return {"graph_uid": graph.graph_uid, "title": title, "session_id": msg.session_id}


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
