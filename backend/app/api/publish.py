"""对外发布：渠道管理（admin）与 OpenAI 兼容公开端点。

公开端点不挂登录依赖（豁免方式与分享 / Webhook 一致），鉴权靠
`Authorization: Bearer pk-…` 的原文哈希比对；对外只回文本，
思考 / 工具 / 审批帧不进入 OpenAI 响应。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_db
from app.models_orm.channel import PublishedChannel
from app.services import chat_service, publish_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["publish"])
public_router = APIRouter(tags=["publish-openai"])


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    # 留空 = 用全局默认模型
    provider: str = ""
    model: str = ""
    # 0 = 永不过期
    expires_in_hours: int = Field(default=0, ge=0)


class ChannelUpdateRequest(BaseModel):
    enabled: bool


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    stream_options: dict[str, Any] | None = None


async def _get_channel_or_404(db: AsyncSession, channel_id: str) -> PublishedChannel:
    try:
        return await publish_service.get_channel(db, channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/publish/channels")
async def list_channels(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_admin),
) -> dict:
    items = await publish_service.list_channels(db)
    return {"items": items, "total": len(items)}


@router.post("/publish/channels")
async def create_channel(
    body: ChannelCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        raw_key, row = await publish_service.create_channel(
            db,
            name=body.name,
            agent_id=body.agent_id,
            provider=body.provider,
            model=body.model,
            expires_in_hours=body.expires_in_hours,
            created_by=user.id,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="publish.create",
        resource_type="published_channel",
        resource_id=row.id,
        detail={
            "name": row.name,
            "agent_id": row.agent_id,
            "provider": row.provider,
            "model": row.model,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    return {**publish_service.channel_to_wire(row), "key": raw_key}


@router.post("/publish/channels/{channel_id}/rotate")
async def rotate_channel_key(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        raw_key, row = await publish_service.rotate_key(db, channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="publish.rotate",
        resource_type="published_channel",
        resource_id=row.id,
        detail={"name": row.name},
    )
    return {"key": raw_key, "channel": publish_service.channel_to_wire(row)}


@router.patch("/publish/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        row = await publish_service.set_enabled(db, channel_id, body.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="publish.update",
        resource_type="published_channel",
        resource_id=row.id,
        detail={"name": row.name, "enabled": row.enabled},
    )
    return publish_service.channel_to_wire(row)


@router.delete("/publish/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        row = await publish_service.get_channel(db, channel_id)
        name = row.name
        await publish_service.delete_channel(db, channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="publish.delete",
        resource_type="published_channel",
        resource_id=channel_id,
        detail={"name": name},
    )
    return {"ok": True}


async def require_channel(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> PublishedChannel:
    """Bearer Key 鉴权；失败统一 401（OpenAI 客户端按此重试/报错）。"""

    if authorization is None or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="缺少 Bearer API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw_key = authorization[7:].strip()
    try:
        return await publish_service.resolve_channel(db, raw_key)
    except publish_service.ChannelAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """取最后一条 user 消息的文本；数组内容只拼接 text 片段。"""

    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("text", "input_text")
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
            return "\n".join(parts).strip()
        raise HTTPException(status_code=400, detail="messages 的 content 格式不支持")
    return ""


def _usage_wire(raw: object) -> dict[str, int] | None:
    """OpenAI usage 必须在三个字段都拿到时才输出，缺字段宁可不报。"""

    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    total = raw.get("total_tokens")
    if not all(isinstance(value, int) for value in (prompt, completion, total)):
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
    }
    if usage is None:
        payload["choices"] = [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ]
    else:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _frames(
    db: AsyncSession,
    channel: PublishedChannel,
    prompt: str,
):
    """跑一轮渠道会话；产出业务 SSE 帧（供流式/非流式共用）。"""

    session_id = await publish_service.ensure_session(db, channel)
    async for frame in chat_service.stream_session_message(
        db,
        session_id=session_id,
        content=prompt,
        provider=None,
        model=None,
        client_message_id=None,
        thinking_enabled=False,
        web_search_enabled=False,
        attachment_ids=[],
        locale=None,
        mentions=None,
        channel=channel.name,
    ):
        yield frame


@public_router.post("/v1/chat/completions")
async def create_chat_completion(
    body: ChatCompletionRequest,
    channel: PublishedChannel = Depends(require_channel),
    db: AsyncSession = Depends(get_db),
):
    prompt = _last_user_text(body.messages)
    if prompt == "":
        raise HTTPException(status_code=400, detail="messages 缺少非空 user 内容")

    include_usage = bool(
        body.stream_options and body.stream_options.get("include_usage")
    )

    if body.stream:
        async def gen():
            completion_id = f"chatcmpl-{uuid4().hex}"
            created = int(time.time())
            usage: dict[str, int] | None = None
            error = ""
            yield _chunk(
                completion_id=completion_id,
                created=created,
                model=channel.model,
                delta={"role": "assistant", "content": ""},
            )
            # 同一渠道复用同一会话：串行化，避免并发写入撞 ADK stale session
            async with publish_service.channel_lock(channel.id):
                try:
                    async for frame in _frames(db, channel, prompt):
                        if frame.event == "assistant_delta":
                            text = frame.data.get("delta")
                            if isinstance(text, str) and text != "":
                                yield _chunk(
                                    completion_id=completion_id,
                                    created=created,
                                    model=channel.model,
                                    delta={"content": text},
                                )
                        elif frame.event == "approval_pending":
                            error = "该渠道的助理需要人工审批，暂不支持对外发布"
                        elif frame.event == "error":
                            error = str(frame.data.get("error") or "回合失败")
                        elif frame.event == "done":
                            usage = _usage_wire(frame.data.get("usage"))
                except Exception:
                    logger.exception("发布渠道流式回合异常 channel=%s", channel.name)
                    error = "回合执行失败"
            if error != "":
                yield (
                    "data: "
                    + json.dumps(
                        {"error": {"message": error, "type": "upstream_error"}},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            else:
                yield _chunk(
                    completion_id=completion_id,
                    created=created,
                    model=channel.model,
                    delta={},
                    finish_reason="stop",
                )
                if include_usage and usage is not None:
                    yield _chunk(
                        completion_id=completion_id,
                        created=created,
                        model=channel.model,
                        delta={},
                        usage=usage,
                    )
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    text_parts: list[str] = []
    usage = None
    error = ""
    async with publish_service.channel_lock(channel.id):
        try:
            async for frame in _frames(db, channel, prompt):
                if frame.event == "assistant_delta":
                    text = frame.data.get("delta")
                    if isinstance(text, str):
                        text_parts.append(text)
                elif frame.event == "approval_pending":
                    error = "该渠道的助理需要人工审批，暂不支持对外发布"
                elif frame.event == "error":
                    error = str(frame.data.get("error") or "回合失败")
                elif frame.event == "done":
                    usage = _usage_wire(frame.data.get("usage"))
        except Exception:
            logger.exception("发布渠道回合异常 channel=%s", channel.name)
            error = "回合执行失败"
    if error != "":
        raise HTTPException(status_code=502, detail=error)

    payload: dict[str, Any] = {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": channel.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(text_parts)},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


@public_router.get("/v1/models")
async def list_published_models(
    channel: PublishedChannel = Depends(require_channel),
) -> dict:
    created = (
        int(channel.created_at.timestamp()) if channel.created_at is not None else 0
    )
    return {
        "object": "list",
        "data": [
            {
                "id": channel.model,
                "object": "model",
                "created": created,
                "owned_by": "ongrid",
            }
        ],
    }
