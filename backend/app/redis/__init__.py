"""Redis 连接收口：全项目唯一的 redis 客户端入口。

强依赖：lifespan 启动时 connect()（含 ping 探活），连不上即启动失败；
业务代码一律 get_redis() 取连接，禁止散落 redis.from_url。
权威业务数据仍在 DB，Redis 只承载事件分发、对话流帧、锁与共享状态。
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Redis | None = None


async def connect() -> Redis:
    """建立连接并探活；失败直接抛出，让 lifespan 启动失败而不是带病运行。"""

    global _client
    if _client is not None:
        return _client
    url = get_settings().redis_url
    client = aioredis.from_url(url, decode_responses=True)
    await client.ping()
    _client = client
    logger.info("Redis 已连接 url=%s", _redact(url))
    return _client


async def close() -> None:
    """关闭连接；未初始化时静默（对称 lifespan 关停）。"""

    global _client
    if _client is None:
        return
    await _client.aclose()
    _client = None
    logger.info("Redis 已关闭")


def get_redis() -> Redis:
    """取已初始化的连接；未 connect() 即取是编程错误，显式失败。"""

    if _client is None:
        raise RuntimeError("Redis 未初始化：必须在 lifespan 中先 await connect()")
    return _client


def _redact(url: str) -> str:
    """日志脱敏：只保留 scheme://host:port/db，抹掉可能的密码。"""

    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        db = parts.path or ""
        return f"{parts.scheme}://{host}{port}{db}"
    except Exception:  # noqa: BLE001 - 脱敏失败就只返回占位，绝不泄漏原串
        return "<redis-url>"
