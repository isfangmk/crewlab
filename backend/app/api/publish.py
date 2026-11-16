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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_db
from app.models.channel import PublishedChannel
from app.services.chat import chat_service, publish_service
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

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
    """渠道编辑：字段缺省表示不改；provider/model 留空表示回到全局默认。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = None
    model: str | None = None
    # 0 = 永不过期；修改后从当前时间重新计算
    expires_in_hours: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI Chat Completions 入参。

    extra="allow" 是为了把没声明又不支持的参数显式拒绝，而不是静默丢弃；
    工具调用、自定义采样等当前渠道链路无法生效的参数一律 400。
    """

    model_config = ConfigDict(extra="allow")

    model: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    n: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    user: str | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    functions: list[dict[str, Any]] | None = None
    tool_choice: Any = None


def _validate_completion_request(
    body: ChatCompletionRequest, channel: PublishedChannel
) -> None:
    """拒绝无法生效的参数：宁可在请求边界显式失败，也不静默改变语义。"""

    unsupported: list[str] = []
    if body.n is not None and body.n != 1:
        unsupported.append("n 仅支持 1")
    if body.temperature not in (None, 1):
        unsupported.append("temperature 仅支持默认值 1")
    if body.top_p not in (None, 1):
        unsupported.append("top_p 仅支持默认值 1")
    if body.presence_penalty not in (None, 0):
        unsupported.append("presence_penalty 仅支持默认值 0")
    if body.frequency_penalty not in (None, 0):
        unsupported.append("frequency_penalty 仅支持默认值 0")
    if body.max_tokens is not None:
        unsupported.append("max_tokens")
    if body.max_completion_tokens is not None:
        unsupported.append("max_completion_tokens")
    if body.stop not in (None, "", []):
        unsupported.append("stop")
    if body.seed is not None:
        unsupported.append("seed")
    if body.logprobs:
        unsupported.append("logprobs")
    if body.top_logprobs is not None:
        unsupported.append("top_logprobs")
    if body.response_format is not None:
        unsupported.append("response_format")
    if body.tools:
        unsupported.append("tools")
    if body.functions:
        unsupported.append("functions")
    if body.tool_choice is not None:
        unsupported.append("tool_choice")
    extras = sorted((body.model_extra or {}).keys())
    if extras:
        unsupported.append("未知参数 " + ", ".join(extras))
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail="当前渠道不支持的请求参数：" + "；".join(unsupported),
        )

    if body.model != "" and body.model != channel.model:
        raise HTTPException(
            status_code=404, detail=f"模型不存在: {body.model}"
        )

    if body.stream_options is not None:
        if not body.stream:
            raise HTTPException(
                status_code=400, detail="stream_options 只能用于流式请求"
            )
        unknown = sorted(
            key for key in body.stream_options if key != "include_usage"
        )
        if unknown:
            raise HTTPException(
                status_code=400,
                detail="stream_options 不支持的字段：" + ", ".join(unknown),
            )
        include_usage = body.stream_options.get("include_usage")
        if include_usage is not None and not isinstance(include_usage, bool):
            raise HTTPException(
                status_code=400, detail="stream_options.include_usage 必须是布尔值"
            )


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


@router.get("/publish/channels/{channel_id}")
async def get_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        return await publish_service.get_channel_detail(db, channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    patch = body.model_dump(exclude_unset=True)
    # provider/model 是一个整体配置：只改一半会让渠道落到不一致的模型
    if ("provider" in patch) != ("model" in patch):
        raise HTTPException(
            status_code=400, detail="provider 与 model 必须同时提供或同时留空"
        )
    try:
        row = await publish_service.update_channel(db, channel_id, **patch)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="publish.update",
        resource_type="published_channel",
        resource_id=row.id,
        detail={
            "name": row.name,
            "enabled": row.enabled,
            "agent_id": row.agent_id,
            "model": row.model,
        },
    )
    usage = await publish_service.get_channel_detail(db, row.id)
    return usage


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
    if (
        not isinstance(prompt, int)
        or not isinstance(completion, int)
        or not isinstance(total, int)
    ):
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
    include_usage: bool = False,
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
        # OpenAI 约定：include_usage 时每个 chunk 都带 usage 字段，普通块为 null
        if include_usage:
            payload["usage"] = None
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
    _validate_completion_request(body, channel)
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
                include_usage=include_usage,
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
                                    include_usage=include_usage,
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
                    include_usage=include_usage,
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
