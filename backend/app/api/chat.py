"""会话与流式对话 API。"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
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
    UpdateSessionTitleRequest,
)
from app.services import attachment_service, chat_service, share_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


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
        message_count=0 if stats is None else stats.message_count,
        tool_call_count=0 if stats is None else stats.tool_call_count,
        last_message_at=(
            None if stats is None or stats.last_message_at is None
            else stats.last_message_at.isoformat()
        ),
        last_message_preview=None if stats is None else stats.last_message_preview,
        total_tokens=None if stats is None else stats.total_tokens,
        last_turn_status=None if stats is None else stats.last_turn_status,
        pending_approvals=0 if stats is None else stats.pending_approvals,
    )


@router.get("/chat/sessions")
async def list_sessions(
    q: str | None = Query(None),
    include_archived: bool = Query(False),
    agent_id: str | None = Query(None),
    model: str | None = Query(None),
    updated_from: datetime | None = Query(None),
    updated_to: datetime | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("desc"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
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
    try:
        deleted = await chat_service.batch_delete_sessions(db, body.ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="session.batch_delete",
        resource_type="chat_session",
        resource_id=",".join(body.ids),
        detail={"count": deleted},
    )
    return {"deleted": deleted}


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionOut)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ChatSessionOut:
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
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


@router.patch("/chat/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: UpdateSessionTitleRequest,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """重命名会话。前端 renameSession 只发 title，契约与之一一对应。"""

    try:
        await chat_service.rename_session(db, session_id, body.title)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {}


@router.patch("/chat/sessions/{session_id}/flags", response_model=ChatSessionOut)
async def patch_session_flags(
    session_id: str,
    body: UpdateSessionFlagsRequest,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
    try:
        session = await chat_service.update_session_flags(
            db, session_id, pinned=body.pinned, archived=body.archived
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


@router.post("/chat/sessions/{session_id}/fork", response_model=ChatSessionOut)
async def fork_session(
    session_id: str,
    body: ForkSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> ChatSessionOut:
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
    return _session_out(session, stats.get(session.id))


@router.get("/chat/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> PlainTextResponse:
    try:
        md = await chat_service.export_session_markdown(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.post("/chat/sessions/{session_id}/share")
async def create_share(
    session_id: str,
    body: CreateShareRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
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
        db,
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
    token: str,
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
    session_id: str,
    body: UpdateSessionModelRequest,
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
        db,
        actor_user_id=user.id,
        action="session.model",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"provider": body.provider, "model": body.model},
    )
    return {}


@router.patch("/chat/sessions/{session_id}/agent")
async def patch_session_agent(
    session_id: str,
    body: UpdateSessionAgentRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        await chat_service.update_session_agent(db, session_id, body.agent_id)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 换助理会改变该会话可见的工具/技能面，属于能力变更。
    await write_audit(
        db,
        actor_user_id=user.id,
        action="session.agent",
        resource_type="chat_session",
        resource_id=session_id,
        detail={"agent_id": body.agent_id},
    )
    return {}


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        await chat_service.delete_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="session.delete",
        resource_type="chat_session",
        resource_id=session_id,
    )
    return {}


@router.post("/chat/sessions/{session_id}/attachments")
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
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
    return attachment_service.attachment_to_wire(row)


@router.get("/chat/sessions/{session_id}/attachments")
async def list_attachments(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = await attachment_service.list_attachments(db, session_id)
    items = [attachment_service.attachment_to_wire(r) for r in rows]
    return {"items": items, "total": len(items)}


@router.get("/chat/sessions/{session_id}/attachments/{attachment_id}")
async def download_attachment(
    session_id: str,
    attachment_id: str,
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
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await chat_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await chat_service.stop_session(session_id)


@router.get("/chat/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
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
    session_id: str,
    body: StreamMessageRequest,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_not_viewer),
):
    set_session_id(session_id)

    async def gen():
        try:
            async for frame in chat_service.stream_session_message(
                db,
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
            ):
                yield {
                    "event": frame.event,
                    "data": json.dumps(frame.data, ensure_ascii=False),
                }
        except LookupError as exc:
            logger.exception("流式回合失败 session_id=%s", session_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        except Exception as exc:
            logger.exception("流式回合异常 session_id=%s", session_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    # sep="\n"：与前端按 \\n\\n 切帧一致，避免 \\r\\n\\r\\n 导致整段缓冲
    return _sse_from_stream(gen())


@router.post("/chat/sessions/{session_id}/messages/edit-resend")
async def edit_and_resend(
    session_id: str,
    body: TruncateAndResendRequest,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_not_viewer),
):
    """编辑用户消息：截断后续并按新正文重发。"""

    set_session_id(session_id)

    async def gen():
        try:
            await chat_service.prepare_resend_from_user_message(
                db, session_id, body.message_id
            )
            async for frame in chat_service.stream_session_message(
                db,
                session_id=session_id,
                content=body.content,
                provider=None,
                model=None,
                client_message_id=None,
                thinking_enabled=body.thinking_enabled,
                web_search_enabled=body.web_search_enabled,
                attachment_ids=body.attachment_ids,
            ):
                yield {
                    "event": frame.event,
                    "data": json.dumps(frame.data, ensure_ascii=False),
                }
        except LookupError as exc:
            logger.exception("编辑重发失败 session_id=%s", session_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        except ValueError as exc:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        except Exception as exc:
            logger.exception("编辑重发异常 session_id=%s", session_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    return _sse_from_stream(gen())
