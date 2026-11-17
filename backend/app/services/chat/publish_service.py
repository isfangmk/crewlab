"""对外发布渠道：签发 Key、解析鉴权、复用会话与按渠道聚合用量。

渠道是「一把 Key + 一个助理 + 一个持久会话」的组合；外部走 OpenAI 兼容
端点时只回文本，工具/思考/审批帧不外泄。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import PublishedChannel
from app.models.chat import ChatSession
from app.models.observation import ObservationTurn
from app.services.agent import agent_service
from app.services.chat import chat_service
from app.services.platform.auth_service import hash_token
from app.services.platform.llm_config import get_default_selection, resolve_model
from app.services.platform.redis_distributed import RedisLock

logger = logging.getLogger(__name__)

KEY_PREFIX = "pk-"
_MAX_EXPIRES_HOURS = 24 * 365 * 5

# 同一渠道的回合串行执行：渠道复用同一个 ADK 会话，并发写入会撞 stale session。
# 用 Redis 分布式锁，多 worker 下也串行（此前进程内 Lock 仅单进程有效）。


def channel_lock(channel_id: str) -> RedisLock:
    """取渠道级互斥锁（async with）；跨 worker 串行化。"""

    return RedisLock(f"channel:{channel_id}", ttl_ms=600_000)


class ChannelAuthError(Exception):
    """渠道 Key 鉴权失败（无效 / 停用 / 过期）。"""


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


async def update_channel(
    db: AsyncSession,
    channel_id: str,
    *,
    name: str | None = None,
    agent_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    expires_in_hours: int | None = None,
    enabled: bool | None = None,
) -> PublishedChannel:
    """编辑渠道；None = 该字段不变，空串 = provider/model 回到全局默认。"""

    row = await get_channel(db, channel_id)
    old_name = row.name
    if name is not None:
        name = name.strip()
        if name == "":
            raise ValueError("渠道名不能为空")
        if len(name) > 128:
            raise ValueError("渠道名最长 128 个字符")
        if name != row.name:
            await _ensure_name_free(db, name, exclude_id=row.id)
            row.name = name
    if agent_id is not None:
        agent_id = agent_id.strip()
        if agent_id == "":
            raise ValueError("发布助理不能为空")
        await agent_service.get_agent(db, agent_id)
        row.agent_id = agent_id
    if provider is not None or model is not None:
        next_provider = (provider or "").strip()
        next_model = (model or "").strip()
        if (next_provider == "") != (next_model == ""):
            raise ValueError("provider 与 model 必须同时提供或同时留空")
        if next_provider == "":
            next_provider, next_model = await get_default_selection(db)
        else:
            await resolve_model(db, next_provider, next_model)
        if next_provider != row.provider or next_model != row.model:
            row.provider = next_provider
            row.model = next_model
            # 会话创建时固化了 provider/model，换模型必须换会话
            row.session_id = None
    if expires_in_hours is not None:
        if expires_in_hours < 0 or expires_in_hours > _MAX_EXPIRES_HOURS:
            raise ValueError(
                f"有效期小时数范围是 0..{_MAX_EXPIRES_HOURS}（0 = 永不过期）"
            )
        row.expires_at = (
            None
            if expires_in_hours == 0
            else datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        )
    if enabled is not None:
        row.enabled = enabled
    if row.name != old_name:
        # 观测用量按渠道名聚合：改名要同步历史回合和会话标题，否则统计断档
        await db.execute(
            update(ObservationTurn)
            .where(ObservationTurn.channel == old_name)
            .values(channel=row.name)
        )
        if row.session_id:
            await db.execute(
                update(ChatSession)
                .where(ChatSession.id == row.session_id)
                .values(title=f"[发布] {row.name}")
            )
    await db.commit()
    await db.refresh(row)
    logger.info(
        "更新发布渠道 id=%s name=%s agent=%s model=%s enabled=%s expires_at=%s",
        row.id,
        row.name,
        row.agent_id,
        row.model,
        row.enabled,
        row.expires_at.isoformat() if row.expires_at else "never",
    )
    return row


async def delete_channel(db: AsyncSession, channel_id: str) -> None:
    row = await get_channel(db, channel_id)
    await db.delete(row)
    await db.commit()
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
            "turns": int(turns or 0),
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(total or 0),
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


async def get_channel_detail(db: AsyncSession, channel_id: str) -> dict[str, object]:
    """单渠道详情：带按渠道聚合的用量，编辑弹窗与列表共用同一份出参。"""

    row = await get_channel(db, channel_id)
    usage = await _usage_by_channel(db)
    return channel_to_wire(row, usage.get(row.name))


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
    """取渠道复用的会话；会话被删除时重建并回写渠道。

    并发请求在等渠道锁之前拿到的 channel 可能已经是旧快照（另一个请求刚
    建好会话），锁内必须先重读，否则会重复建会话并覆盖 session_id。
    """

    await db.refresh(channel)
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
