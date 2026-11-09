"""会话与流式对话 API。"""

from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth
from app.db import get_db
from app.schemas.chat import (
    ChatSessionOut,
    CreateSessionRequest,
    StreamMessageRequest,
    UpdateSessionAgentRequest,
    UpdateSessionModelRequest,
    UpdateSessionTitleRequest,
)
from app.services import attachment_service, chat_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _session_out(s) -> ChatSessionOut:
    return ChatSessionOut(
        id=s.id,
        title=s.title,
        provider=s.provider,
        model=s.model,
        agent_id=s.agent_id,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        page_context=chat_service.session_page_context(s),
    )


@router.get("/chat/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await chat_service.list_sessions(db)
    return {"items": [_session_out(s) for s in items], "total": len(items)}


@router.post("/chat/sessions", response_model=ChatSessionOut)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_auth),
) -> ChatSessionOut:
    try:
        session = await chat_service.create_session(
            db,
            title=body.title,
            provider=body.provider,
            model=body.model,
            agent_id=body.agent_id,
            user_id=user,
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
    _user: str = Depends(require_auth),
) -> dict:
    """重命名会话。前端 renameSession 只发 title，契约与之一一对应。"""

    try:
        await chat_service.rename_session(db, session_id, body.title)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {}


@router.put("/chat/sessions/{session_id}/model")
async def put_session_model(
    session_id: str,
    body: UpdateSessionModelRequest,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """切换会话模型；provider+model 必须在「配置-模型」中可反查。"""

    try:
        await chat_service.update_session_model(
            db, session_id, body.provider, body.model
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {}


@router.patch("/chat/sessions/{session_id}/agent")
async def patch_session_agent(
    session_id: str,
    body: UpdateSessionAgentRequest,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await chat_service.update_session_agent(db, session_id, body.agent_id)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {}


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await chat_service.delete_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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


@router.post("/chat/sessions/{session_id}/messages/stream")
async def stream_message(
    session_id: str,
    body: StreamMessageRequest,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
):
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
    return EventSourceResponse(gen(), sep="\n")
