"""会话与流式对话 API。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.request_context import set_session_id
from app.schemas.chat import (
    BatchDeleteSessionsRequest,
    ChatSessionOut,
    CreateSessionRequest,
    CreateShareRequest,
    ForkSessionRequest,
    StreamMessageRequest,
    TruncateAndResendRequest,
    UpdateSessionAgentRequest,
    UpdateSessionFlagsRequest,
    UpdateSessionModelRequest,
    UpdateSessionPrefsRequest,
    UpdateSessionTitleRequest,
)
from app.services.chat import (
    attachment_service,
    chat_queue,
    chat_service,
    chat_stream_bus,
    chat_stream_orchestrator,
    share_service,
)
from app.services.observability.audit_service import write_audit
from app.services.platform import notification_bus
from app.services.platform.auth_service import AuthUser

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class QueueMessageRequest(BaseModel):
    """回答进行中发送的消息入队请求体；字段足够后续起回合复用。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="要入队的消息正文，至少 1 个字符")
    provider: str | None = Field(default=None, description="本条消息覆盖的大模型提供方；留空则用会话设置")
    model: str | None = Field(default=None, description="本条消息覆盖的大模型名称；留空则用会话设置")
    thinking_enabled: bool = Field(default=False, description="本条消息是否开启思考模式")
    web_search_enabled: bool = Field(default=False, description="本条消息是否开启联网搜索")
    attachment_ids: list[str] = Field(default_factory=list, description="本条消息携带的图片附件 id 列表")
    mentions: list[dict[str, Any]] = Field(default_factory=list, description="本条消息 @ 提及的对象列表，后端按 id 反查并注入上下文")


class UpdateQueueItemIn(BaseModel):
    """原地修改一条队列消息的正文。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="替换后的消息正文，至少 1 个字符")


def _require_session_owner(session, user: AuthUser) -> None:
    """校验会话属当前用户；admin 放行。

    属主以 owner_user_id 为准；历史会话该列可能为空，回退到 adk_user_id（建会话时
    置为 str(user.id)）判断，避免误伤老数据。
    """

    if user.role == "admin":
        return
    owner_ok = session.owner_user_id == user.id or (
        session.owner_user_id is None and session.adk_user_id == str(user.id)
    )
    if not owner_ok:
        raise HTTPException(status_code=403, detail="无权操作该会话")


def _session_out(s, stats: chat_service.SessionStats | None = None) -> ChatSessionOut:
    """会话出参；stats 缺失时按「新会话」处理（0 条消息、无摘要）。"""

    return ChatSessionOut(
        id=s.id,
        title=s.title,
        provider=s.provider,
        model=s.model,
        agent_id=s.agent_id,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        page_context=chat_service.session_page_context(s),
        pinned=bool(s.pinned),
        archived=bool(s.archived),
        thinking_enabled=bool(s.thinking_enabled),
        web_search_enabled=bool(s.web_search_enabled),
        message_count=0 if stats is None else stats.message_count,
        turn_count=0 if stats is None else stats.turn_count,
        tool_call_count=0 if stats is None else stats.tool_call_count,
        skill_call_count=0 if stats is None else stats.skill_call_count,
        last_message_at=(
            None if stats is None or stats.last_message_at is None
            else stats.last_message_at.isoformat()
        ),
        last_message_preview=None if stats is None else stats.last_message_preview,
        total_tokens=None if stats is None else stats.total_tokens,
        last_turn_status=None if stats is None else stats.last_turn_status,
        pending_approvals=0 if stats is None else stats.pending_approvals,
        pending_inputs=0 if stats is None else stats.pending_inputs,
        attachment_count=0 if stats is None else stats.attachment_count,
        artifact_count=0 if stats is None else stats.artifact_count,
        shared=False if stats is None else stats.shared,
    )


@router.get("/chat/sessions")
async def list_sessions(
    q: str | None = Query(default=None, description="按会话标题关键字模糊筛选；留空不过滤"),
    include_archived: bool = Query(default=False, description="是否包含已归档会话；false 仅返回未归档"),
    agent_id: str | None = Query(default=None, description="按绑定助理 id 精确筛选；留空不过滤"),
    model: str | None = Query(default=None, description="按使用的大模型名精确筛选；留空不过滤"),
    updated_from: datetime | None = Query(default=None, description="只返回更新时间不早于该时刻的会话；留空不限起点"),
    updated_to: datetime | None = Query(default=None, description="只返回更新时间不晚于该时刻的会话；留空不限终点"),
    sort_by: str | None = Query(default=None, description="排序字段：title/model/updated_at/last_message_at/agent_id/created_at；留空按最近消息时间"),
    sort_dir: str = Query(default="desc", description="排序方向：desc 降序、asc 升序"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，范围 1~100"),
    offset: int = Query(default=0, ge=0, description="分页偏移量，从 0 开始"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """分页列出当前用户的会话（含统计摘要）；支持关键字、助理、模型、时间与归档筛选。"""

    try:
        items, total = await chat_service.list_sessions(
            db,
            q=q,
            include_archived=include_archived,
            agent_id=agent_id,
            model=model,
            updated_from=updated_from,
            updated_to=updated_to,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stats = await chat_service.list_session_stats(db, [s.id for s in items])
    return {
        "items": [_session_out(s, stats.get(s.id)) for s in items],
        "total": total,
    }


@router.get("/chat/messages/search")
async def search_messages(
    q: str = Query(..., min_length=1, description="要搜索的消息正文关键字，至少 1 个字符"),
    limit: int = Query(default=20, ge=1, le=100, description="每页条数，范围 1~100"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """跨会话搜索消息正文：按会话聚合命中片段，供前端定位跳转。

    属主过滤在 service 内按 user.id 收口（admin 放行），防止跨用户内容泄露。
    """
    try:
        items = await chat_service.search_messages(
            db, q=q, limit=limit, owner_user_id=user.id, is_admin=user.role == "admin"
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/chat/sessions/models")
async def list_session_models(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """会话列表的模型筛选项；必须声明在 /chat/sessions/{id} 之前，否则会被当成 session_id。"""

    return {"items": await chat_service.list_session_models(db)}


@router.post("/chat/sessions/batch-delete")
async def batch_delete_sessions(
    body: BatchDeleteSessionsRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """批量硬删除多个会话；ids 必须全部存在，否则整批失败。"""
    try:
        deleted = await chat_service.batch_delete_sessions(db, body.ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.batch_delete",
        resource_type="chat_session",
        # resource_id 定长 128，多个 UUID 拼进去会溢出（4 个就 146 字符）；
        # 批次不是单条资源，id 列表放 detail，resource_id 留空
        resource_id="",
        detail={"count": deleted, "ids": body.ids},
    )
    return {"deleted": deleted}


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionOut)
async def get_session(
    session_id: str = Path(description="要查看的会话 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ChatSessionOut:
    """按 id 获取单个会话详情（含统计摘要）；无权或不存在返回 403/404。"""
    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    stats = await chat_service.list_session_stats(db, [session.id])
    return _session_out(session, stats.get(session.id))


@router.post("/chat/sessions", response_model=ChatSessionOut)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
    """新建一个对话会话，返回其摘要信息。"""
    try:
        session = await chat_service.create_session(
            db,
            title=body.title,
            provider=body.provider,
            model=body.model,
            agent_id=body.agent_id,
            user_id=str(user.id),
            owner_user_id=user.id,
            page_context=(
                body.page_context.model_dump() if body.page_context is not None else None
            ),
            thinking_enabled=body.thinking_enabled,
            web_search_enabled=body.web_search_enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.create",
        resource_type="chat_session",
        resource_id=session.id,
        detail={"title": session.title, "provider": session.provider, "model": session.model},
    )
    return _session_out(session)


@router.patch("/chat/sessions/{session_id}")
async def patch_session(
    session_id: str = Path(description="要重命名的会话 id"),
    body: UpdateSessionTitleRequest = Body(description="新的会话标题"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """重命名会话。前端 renameSession 只发 title，契约与之一一对应。"""

    try:
        await chat_service.rename_session(db, session_id, body.title)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.rename",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"title": body.title},
    )
    return {}


@router.patch("/chat/sessions/{session_id}/flags", response_model=ChatSessionOut)
async def patch_session_flags(
    session_id: str = Path(description="要更新标记的会话 id"),
    body: UpdateSessionFlagsRequest = Body(description="要更新的置顶/归档标记，只更新显式提供的字段"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
    """更新会话的置顶/归档标记，返回更新后的会话摘要。"""
    try:
        session = await chat_service.update_session_flags(
            db, session_id, pinned=body.pinned, archived=body.archived
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.update",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"pinned": body.pinned, "archived": body.archived},
    )
    return _session_out(session)


@router.patch("/chat/sessions/{session_id}/prefs", response_model=ChatSessionOut)
async def patch_session_prefs(
    session_id: str = Path(description="要更新开关的会话 id"),
    body: UpdateSessionPrefsRequest = Body(description="要更新的思考/联网搜索开关，至少提供一项"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
    """「思考 / 联网搜索」开关写回会话数据，换页面打开即同步。"""

    try:
        session = await chat_service.update_session_prefs(
            db,
            session_id,
            thinking_enabled=body.thinking_enabled,
            web_search_enabled=body.web_search_enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.update",
        resource_type="chat_session",
        resource_id=session_id,
        detail={
            "thinking_enabled": body.thinking_enabled,
            "web_search_enabled": body.web_search_enabled,
        },
    )
    return _session_out(session)


@router.post("/chat/sessions/{session_id}/fork", response_model=ChatSessionOut)
async def fork_session(
    session_id: str = Path(description="要分叉的源会话 id"),
    body: ForkSessionRequest = Body(description="分叉到的截止消息"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
    """从源会话的某条消息分叉出新会话，消息复制到该消息（含）为止。"""
    try:
        session = await chat_service.fork_session(
            db,
            session_id,
            up_to_message_id=body.up_to_message_id,
            owner_user_id=user.id,
            adk_user_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # fork 会把消息复制到新会话，统计必须按新会话 id 重新算，不能沿用源会话。
    stats = await chat_service.list_session_stats(db, [session.id])
    await write_audit(
        actor_user_id=user.id,
        action="session.fork",
        resource_type="chat_session",
        resource_id=session.id,
        detail={"from_session_id": session_id, "up_to_message_id": body.up_to_message_id},
    )
    return _session_out(session, stats.get(session.id))


@router.get("/chat/sessions/{session_id}/export")
async def export_session(
    session_id: str = Path(description="要导出的会话 id"),
    format: str = Query(default="markdown", pattern="^(markdown|json)$", description="导出格式：markdown 为文档，json 为结构化消息"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> Response:
    """会话导出：markdown 为文档；json 为结构化消息（与只读分享同出参形状）。"""

    try:
        if format == "json":
            session = await chat_service.get_session(db, session_id)
            messages = await chat_service.list_messages(db, session_id)
            items = await chat_service.messages_to_wire(db, messages)
            payload = {"session": _session_out(session), "items": items, "total": len(items)}
            return Response(
                content=json.dumps(jsonable_encoder(payload), ensure_ascii=False, indent=2),
                media_type="application/json; charset=utf-8",
            )
        md = await chat_service.export_session_markdown(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.post("/chat/sessions/{session_id}/share")
async def create_share(
    session_id: str = Path(description="要分享的会话 id"),
    body: CreateShareRequest = Body(description="分享有效期"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """为会话创建只读分享链接，返回明文 token（仅此一次）、过期时间与会话 id。"""
    try:
        await chat_service.get_session(db, session_id)
        raw, row = await share_service.create_share(
            db,
            session_id=session_id,
            created_by=user.id,
            expires_in_hours=body.expires_in_hours,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.share",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"expires_in_hours": body.expires_in_hours},
    )
    return {
        "token": raw,
        "expires_at": row.expires_at.isoformat(),
        "session_id": session_id,
    }


@router.get("/chat/shares/{token}")
async def read_share(
    token: str = Path(description="分享链接中的明文 token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """只读分享：无需登录。"""

    try:
        session_id = await share_service.resolve_share(db, token)
        messages = await chat_service.list_messages(db, session_id)
        items = await chat_service.messages_to_wire(db, messages)
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "session": _session_out(session),
        "items": items,
        "total": len(items),
    }


@router.put("/chat/sessions/{session_id}/model")
async def put_session_model(
    session_id: str = Path(description="要切换模型的会话 id"),
    body: UpdateSessionModelRequest = Body(description="新的模型提供方与名称，须成对提供"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """切换会话模型；provider+model 必须在「配置-模型」中可反查。"""

    try:
        await chat_service.update_session_model(
            db, session_id, body.provider, body.model
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 切模型等于改变数据流向哪家厂商，属于治理动作。
    await write_audit(
        actor_user_id=user.id,
        action="session.model",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"provider": body.provider, "model": body.model},
    )
    return {}


@router.patch("/chat/sessions/{session_id}/agent")
async def patch_session_agent(
    session_id: str = Path(description="要切换助理的会话 id"),
    body: UpdateSessionAgentRequest = Body(description="新的助理 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """切换会话绑定的智能体，改变该会话可见的工具/技能面。"""
    try:
        await chat_service.update_session_agent(db, session_id, body.agent_id)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 换助理会改变该会话可见的工具/技能面，属于能力变更。
    await write_audit(
        actor_user_id=user.id,
        action="session.agent",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"agent_id": body.agent_id},
    )
    return {}


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: str = Path(description="要删除的会话 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """删除单个会话（含其消息等关联数据），不可恢复。"""
    try:
        await chat_service.delete_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.delete",
        resource_type="chat_session",
        resource_id=session_id,
    )
    return {}


@router.post("/chat/sessions/{session_id}/attachments")
async def upload_attachment(
    session_id: str = Path(description="要上传附件的会话 id"),
    file: UploadFile = File(..., description="要上传的图片文件（image/*），用于多模态消息"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """上传会话图片附件；返回附件元数据，id 供发消息时引用。"""

    filename = file.filename
    if filename is None or filename == "":
        raise HTTPException(status_code=400, detail="缺少文件名")
    content_type = file.content_type
    if content_type is None or content_type == "":
        raise HTTPException(status_code=400, detail="缺少文件类型")
    data = await file.read()
    try:
        row = await attachment_service.create_attachment(
            db,
            session_id=session_id,
            name=filename,
            mime_type=content_type,
            data=data,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="session.attachment",
        resource_type="chat_session",
        resource_id=session_id,
        # 审计只记附件外形，图片内容不落审计详情
        detail={"attachment_id": row.id, "name": filename, "mime_type": content_type},
    )
    return attachment_service.attachment_to_wire(row)


@router.get("/chat/sessions/{session_id}/attachments")
async def list_attachments(
    session_id: str = Path(description="要列出附件的会话 id"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """列出会话下的全部图片附件元数据（不含图片内容）。"""
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = await attachment_service.list_attachments(db, session_id)
    items = [attachment_service.attachment_to_wire(r) for r in rows]
    return {"items": items, "total": len(items)}


@router.get("/chat/sessions/{session_id}/attachments/{attachment_id}")
async def download_attachment(
    session_id: str = Path(description="附件所属会话 id"),
    attachment_id: str = Path(description="要下载的附件 id"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> Response:
    """按 id 取附件原始字节，供前端渲染图片。"""

    try:
        row = await attachment_service.get_attachment(db, session_id, attachment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # data_url 形如 data:<mime>;base64,<payload>
    header, _, payload = row.data_url.partition(",")
    if header == "" or payload == "":
        raise HTTPException(status_code=500, detail="附件数据损坏")
    try:
        raw = base64.b64decode(payload)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="附件数据损坏") from exc
    return Response(content=raw, media_type=row.mime_type)


@router.post("/chat/sessions/{session_id}/stop")
async def stop_session(
    session_id: str = Path(description="要停止当前回合的会话 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """请求停止会话当前正在进行的回合。"""
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await chat_service.stop_session(session_id)
    await write_audit(
        actor_user_id=user.id,
        action="session.stop",
        resource_type="chat_session",
        resource_id=session_id,
    )
    return result


@router.post("/chat/sessions/{session_id}/queue")
async def enqueue_message(
    session_id: str = Path(description="要入队消息的会话 id"),
    body: QueueMessageRequest = Body(description="要入队的消息内容"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """回答进行中发送消息：入队等待当前回合结束后依次消费。"""

    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_session_owner(session, user)

    item_id, position = await chat_queue.enqueue(
        session_id,
        content=body.content,
        provider=body.provider,
        model=body.model,
        thinking_enabled=body.thinking_enabled,
        web_search_enabled=body.web_search_enabled,
        attachment_ids=body.attachment_ids,
        mentions=body.mentions,
        owner_uid=session.owner_user_id,
    )
    total = await chat_queue.total(session_id)
    await notification_bus.publish(
        session.owner_user_id,
        kind="queue_changed",
        payload={"session_id": session_id, "action": "enqueued", "total": total},
    )
    await write_audit(
        actor_user_id=user.id,
        action="session.queue_enqueue",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"session_id": session_id, "item_id": item_id, "position": position},
    )
    return {"session_id": session_id, "item_id": item_id, "position": position}


@router.get("/chat/sessions/{session_id}/queue")
async def list_queue(
    session_id: str = Path(description="要列出排队消息的会话 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出会话排队消息（只读，按入队顺序）。"""

    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = await chat_queue.list_items(session_id)
    return {
        "items": [
            {
                "item_id": it.item_id,
                "content": it.content,
                "position": it.position,
                "created_at": it.created_at,
                "interrupt": it.interrupt,
            }
            for it in items
        ],
        "total": len(items),
    }


@router.delete("/chat/sessions/{session_id}/queue/{item_id}")
async def delete_queued_message(
    session_id: str = Path(description="队列消息所属会话 id"),
    item_id: str = Path(description="要删除的队列消息 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """删除一条排队消息。"""

    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_session_owner(session, user)

    # 删前先取位置写审计；找不到视为 404，与 DELETE 语义一致
    position = next(
        (it.position for it in await chat_queue.list_items(session_id) if it.item_id == item_id),
        None,
    )
    if position is None:
        raise HTTPException(status_code=404, detail=f"队列项不存在: {item_id}")

    removed = await chat_queue.delete(session_id, item_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"队列项不存在: {item_id}")
    total = await chat_queue.total(session_id)
    await notification_bus.publish(
        session.owner_user_id,
        kind="queue_changed",
        payload={"session_id": session_id, "action": "deleted", "total": total},
    )
    await write_audit(
        actor_user_id=user.id,
        action="session.queue_delete",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"session_id": session_id, "item_id": item_id, "position": position},
    )
    return {"deleted": item_id}


@router.patch("/chat/sessions/{session_id}/queue/{item_id}")
async def update_queued_message(
    session_id: str = Path(description="队列消息所属会话 id"),
    item_id: str = Path(description="要修改的队列消息 id"),
    body: UpdateQueueItemIn = Body(description="替换后的消息正文"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """原地修改一条排队消息的正文；位置不变，轮到它时按新正文执行。"""

    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_session_owner(session, user)

    if not await chat_queue.update_content(session_id, item_id, body.content):
        raise HTTPException(status_code=404, detail=f"队列项不存在: {item_id}")
    total = await chat_queue.total(session_id)
    await notification_bus.publish(
        session.owner_user_id,
        kind="queue_changed",
        payload={"session_id": session_id, "action": "updated", "total": total},
    )
    await write_audit(
        actor_user_id=user.id,
        action="session.queue_update",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"session_id": session_id, "item_id": item_id},
    )
    return {"session_id": session_id, "item_id": item_id, "content": body.content}


@router.post("/chat/sessions/{session_id}/queue/{item_id}/interject")
async def interject_queued_message(
    session_id: str = Path(description="队列消息所属会话 id"),
    item_id: str = Path(description="要插话的队列消息 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """插话：标记该队列项优先，并打断当前回合让它立即执行。"""

    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_session_owner(session, user)

    if not await chat_queue.mark_interrupt(session_id, item_id, True):
        raise HTTPException(status_code=404, detail=f"队列项不存在: {item_id}")
    interrupted = await chat_stream_orchestrator.request_interrupt(session_id)
    total = await chat_queue.total(session_id)
    await notification_bus.publish(
        session.owner_user_id,
        kind="queue_changed",
        payload={"session_id": session_id, "action": "interjected", "total": total},
    )
    await write_audit(
        actor_user_id=user.id,
        action="session.queue_interject",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"session_id": session_id, "item_id": item_id, "interrupted": interrupted},
    )
    logger.info(
        "队列插话 session_id=%s item_id=%s current_turn_interrupted=%s",
        session_id,
        item_id,
        interrupted,
    )
    return {"session_id": session_id, "item_id": item_id, "interrupted": interrupted}


@router.get("/chat/sessions/{session_id}/messages")
async def get_messages(
    session_id: str = Path(description="要读取消息的会话 id"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """返回与流式渲染同构的消息列表，避免刷新后结构/顺序不一致。"""

    try:
        messages = await chat_service.list_messages(db, session_id)
        items = await chat_service.messages_to_wire(db, messages)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


def _sse_from_stream(gen):
    """把业务 SSE 帧包装成 EventSourceResponse。"""

    return EventSourceResponse(gen, sep="\n")


@router.post("/chat/sessions/{session_id}/messages/stream")
async def stream_message(
    session_id: str = Path(description="要发送消息的会话 id"),
    body: StreamMessageRequest = Body(description="本回合消息内容与覆盖配置"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_not_viewer),
):
    """发起流式回合：登记回合 + 后台生产，立即返回 turn_id。

    帧不随本请求返回：所有订阅者（含发起标签页）统一走 GET .../events 读 Redis
    帧流，支持多标签页补发+续推。本端点不再持有 SSE 连接。
    """
    set_session_id(session_id)
    try:
        session = await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    turn_id = await chat_stream_orchestrator.begin_streamed_turn(
        session_id=session_id,
        content=body.content,
        provider=body.provider,
        model=body.model,
        client_message_id=body.client_message_id,
        thinking_enabled=body.thinking_enabled,
        web_search_enabled=body.web_search_enabled,
        attachment_ids=body.attachment_ids,
        locale=body.locale,
        mentions=[m.model_dump() for m in body.mentions],
        owner_uid=session.owner_user_id,
    )
    return {"session_id": session_id, "turn_id": turn_id}


@router.get("/chat/sessions/{session_id}/active-turn")
async def get_active_turn(
    session_id: str = Path(description="要查询的会话 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
):
    """查会话当前进行中的回合；供第二个标签页打开时 attach 续流。

    无进行中回合返回 {"turn_id": null}。刷新后前端据此决定是否连事件流。
    """

    set_session_id(session_id)
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    active = await chat_stream_bus.active_turn(session_id)
    return {"turn_id": active.get("turn_id") if active else None}


@router.get("/chat/sessions/{session_id}/events")
async def stream_session_events(
    session_id: str = Path(description="要订阅帧流的会话 id"),
    *,
    request: Request,
    turn_id: str = Query(..., description="要订阅的回合 id（POST /messages/stream 返回）"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
):
    """订阅某回合的帧流：Last-Event-ID 或 ?last_id 续传，先补发后实时续推。

    多个标签页可同时订阅同一回合；各自从自己的 last_id 读，互不消费、互不抢帧。
    """

    set_session_id(session_id)
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    header_id = request.headers.get("last-event-id", "")
    query_id = request.query_params.get("last_id", "")
    last_id = header_id or query_id or "0-0"

    async def gen():
        try:
            async for stream_id, event, data in chat_stream_bus.subscribe(
                turn_id, last_id=last_id
            ):
                yield {
                    "event": event,
                    "id": stream_id,
                    "data": json.dumps(data, ensure_ascii=False),
                }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - SSE 流内异常转成 error 帧，避免连接悬空
            logger.exception("会话帧流订阅失败 session_id=%s", session_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    # sep="\n"：与前端按 \n\n 切帧一致；id 字段供 Last-Event-ID 续传
    return _sse_from_stream(gen())


@router.post("/chat/sessions/{session_id}/messages/edit-resend")
async def edit_and_resend(
    session_id: str = Path(description="要编辑重发的会话 id"),
    body: TruncateAndResendRequest = Body(description="要编辑的用户消息与替换正文"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
):
    """编辑用户消息并截断后续后重发：登记回合 + 后台生产，返回 turn_id。"""

    set_session_id(session_id)
    session = await chat_service.get_session(db, session_id)

    async def prepare(db: AsyncSession) -> None:
        await chat_service.prepare_resend_from_user_message(
            db, session_id, body.message_id
        )
        # 截断已落库：审计记编辑重发，正文不进详情（消息本体在 chat_messages）
        await write_audit(
            actor_user_id=user.id,
            action="session.edit_resend",
            resource_type="chat_session",
            resource_id=session_id,
            detail={"message_id": body.message_id},
        )

    turn_id = await chat_stream_orchestrator.begin_streamed_turn(
        session_id=session_id,
        content=body.content,
        provider=None,
        model=None,
        client_message_id=None,
        thinking_enabled=body.thinking_enabled,
        web_search_enabled=body.web_search_enabled,
        attachment_ids=body.attachment_ids,
        locale=None,
        mentions=None,
        owner_uid=session.owner_user_id,
        before_produce=prepare,
    )
    return {"session_id": session_id, "turn_id": turn_id}
