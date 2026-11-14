"""对外发布渠道：签发 Key、解析鉴权、复用会话与按渠道聚合用量。

渠道是「一把 Key + 一个助理 + 一个持久会话」的组合；外部走 OpenAI 兼容
端点时只回文本，工具/思考/审批帧不外泄。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.channel import PublishedChannel
from app.models_orm.observation import ObservationTurn
from app.services import agent_service, chat_service
from app.services.auth_service import hash_token
from app.services.llm_config import get_default_selection, resolve_model

logger = logging.getLogger(__name__)

KEY_PREFIX = "pk-"
_MAX_EXPIRES_HOURS = 24 * 365 * 5

# 同一渠道的回合串行执行：渠道复用同一个 ADK 会话，并发写入会撞 stale session
_channel_locks: dict[str, asyncio.Lock] = {}


class ChannelAuthError(Exception):
    """渠道 Key 鉴权失败（无效 / 停用 / 过期）。"""


def channel_lock(channel_id: str) -> asyncio.Lock:
    """取渠道级互斥锁；进程内有效，多进程部署需外部串行化。"""

    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock


def _issue_key() -> tuple[str, str, str]:
    """生成 (原文, 哈希, 展示前缀)；原文只返回给调用方一次。"""

    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:12]


async def _ensure_name_free(db: AsyncSession, name: str, exclude_id: str = "") -> None:
    row = await db.scalar(
        select(PublishedChannel).where(PublishedChannel.name == name)
    )
    if row is not None and row.id != exclude_id:
        raise ValueError(f"渠道名已存在: {name}")


async def create_channel(
    db: AsyncSession,
    *,
    name: str,
    agent_id: str,
    provider: str,
    model: str,
    expires_in_hours: int,
    created_by: int | None,
) -> tuple[str, PublishedChannel]:
    name = name.strip()
    if name == "":
        raise ValueError("渠道名不能为空")
    if len(name) > 128:
        raise ValueError("渠道名最长 128 个字符")
    if expires_in_hours < 0 or expires_in_hours > _MAX_EXPIRES_HOURS:
        raise ValueError(f"有效期小时数范围是 0..{_MAX_EXPIRES_HOURS}（0 = 永不过期）")
    await _ensure_name_free(db, name)
    # 助理必须存在，否则第一次调用才失败
    await agent_service.get_agent(db, agent_id)
    if provider == "" or model == "":
        provider, model = await get_default_selection(db)
    else:
        await resolve_model(db, provider, model)

    raw, key_hash, key_prefix = _issue_key()
    row = PublishedChannel(
        id=uuid4().hex,
        name=name,
        agent_id=agent_id,
        provider=provider,
        model=model,
        key_hash=key_hash,
        key_prefix=key_prefix,
        expires_at=(
            None
            if expires_in_hours == 0
            else datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        ),
        enabled=True,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建发布渠道 id=%s name=%s agent=%s provider=%s model=%s expires_at=%s",
        row.id,
        row.name,
        row.agent_id,
        row.provider,
        row.model,
        row.expires_at.isoformat() if row.expires_at else "never",
    )
    return raw, row


async def rotate_key(db: AsyncSession, channel_id: str) -> tuple[str, PublishedChannel]:
    row = await get_channel(db, channel_id)
    raw, key_hash, key_prefix = _issue_key()
    row.key_hash = key_hash
    row.key_prefix = key_prefix
    await db.commit()
    await db.refresh(row)
    logger.info("轮换发布渠道 Key id=%s name=%s", row.id, row.name)
    return raw, row


async def set_enabled(
    db: AsyncSession, channel_id: str, enabled: bool
) -> PublishedChannel:
    row = await get_channel(db, channel_id)
    row.enabled = enabled
    await db.commit()
    await db.refresh(row)
    logger.info("发布渠道启停 id=%s name=%s enabled=%s", row.id, row.name, enabled)
    return row


async def delete_channel(db: AsyncSession, channel_id: str) -> None:
    row = await get_channel(db, channel_id)
    await db.delete(row)
    await db.commit()
    _channel_locks.pop(channel_id, None)
    logger.info("删除发布渠道 id=%s name=%s", row.id, row.name)


async def get_channel(db: AsyncSession, channel_id: str) -> PublishedChannel:
    row = await db.get(PublishedChannel, channel_id)
    if row is None:
        raise LookupError(f"发布渠道不存在: {channel_id}")
    return row


async def _usage_by_channel(db: AsyncSession) -> dict[str, dict[str, object]]:
    """按渠道聚合观测回合；没有调用的渠道不出现在结果里。"""

    rows = (
        await db.execute(
            select(
                ObservationTurn.channel,
                func.count(ObservationTurn.id),
                func.coalesce(func.sum(ObservationTurn.prompt_tokens), 0),
                func.coalesce(func.sum(ObservationTurn.completion_tokens), 0),
                func.coalesce(func.sum(ObservationTurn.total_tokens), 0),
                func.max(ObservationTurn.started_at),
            )
            .where(ObservationTurn.channel != "")
            .group_by(ObservationTurn.channel)
        )
    ).all()
    return {
        str(name): {
            "turns": int(turns),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
            "last_used_at": last.isoformat() if last is not None else None,
        }
        for name, turns, prompt, completion, total, last in rows
    }


def channel_to_wire(
    channel: PublishedChannel, usage: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "id": channel.id,
        "name": channel.name,
        "agent_id": channel.agent_id,
        "provider": channel.provider,
        "model": channel.model,
        "key_masked": f"{channel.key_prefix}…",
        "enabled": channel.enabled,
        "expires_at": channel.expires_at.isoformat() if channel.expires_at else None,
        "session_id": channel.session_id,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "usage": usage
        or {
            "turns": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "last_used_at": None,
        },
    }


async def list_channels(db: AsyncSession) -> list[dict[str, object]]:
    rows = (
        await db.scalars(select(PublishedChannel).order_by(PublishedChannel.created_at.desc()))
    ).all()
    usage = await _usage_by_channel(db)
    return [channel_to_wire(row, usage.get(row.name)) for row in rows]


async def resolve_channel(db: AsyncSession, raw_key: str) -> PublishedChannel:
    if not raw_key.startswith(KEY_PREFIX) or len(raw_key) <= len(KEY_PREFIX):
        raise ChannelAuthError("API Key 无效")
    row = await db.scalar(
        select(PublishedChannel).where(PublishedChannel.key_hash == hash_token(raw_key))
    )
    if row is None:
        raise ChannelAuthError("API Key 无效")
    if not row.enabled:
        raise ChannelAuthError("渠道已停用")
    if row.expires_at is not None and row.expires_at < datetime.now(timezone.utc):
        raise ChannelAuthError("渠道已过期")
    return row


async def ensure_session(db: AsyncSession, channel: PublishedChannel) -> str:
    """取渠道复用的会话；会话被删除时重建并回写渠道。"""

    if channel.session_id:
        try:
            session = await chat_service.get_session(db, channel.session_id)
            return session.id
        except LookupError:
            logger.info(
                "发布渠道的会话已不存在，重建 id=%s session=%s",
                channel.id,
                channel.session_id,
            )
    session = await chat_service.create_session(
        db,
        title=f"[发布] {channel.name}",
        provider=channel.provider,
        model=channel.model,
        agent_id=channel.agent_id,
        user_id=f"channel:{channel.id}",
        owner_user_id=channel.created_by,
    )
    channel.session_id = session.id
    await db.commit()
    return session.id
