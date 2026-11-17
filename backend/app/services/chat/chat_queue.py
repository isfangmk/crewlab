"""对话消息队列：按会话用 Redis Stream 缓冲「回答进行中发送」的消息。

回答还在生成时用户再发的消息默认排队（可多条、可删除），当前回合结束后服务端
按入队顺序依次消费，形成链式回合直到队列空。

设计要点：
- 一个会话一条 Stream（chat:queue:{sid}）：天然按 sid 隔离；XRANGE - + 按入队
  顺序返回；Stream ID 即 item_id（单调，可当位置/去重依据）。
- 入队 XADD、列表 XRANGE、删除 XDEL、出队用 Lua（读第一条 + XDEL 原子完成）：
  出队必须原子，否则多 worker 会重复消费同一条消息。
- 每次操作都续 TTL（24h）：队列是临时缓冲，会话长期不活跃自动回收。
- 复杂字段（attachment_ids / mentions）JSON 序列化后存字段，None 用空串占位，
  保证后续能原样起新回合。

key 约定与注释风格对齐 chat_stream_bus.py：
  chat:queue:{sid}  —— Stream，字段见 _encode_item
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.redis import get_redis

logger = logging.getLogger(__name__)

_QUEUE_KEY = "chat:queue:{sid}"
# 队列 TTL（秒）：24h。回答中排队的消息是临时缓冲，长期不活跃自动回收
_QUEUE_TTL = 24 * 3600

# 原子出队：读第一条并 XDEL，防多 worker 重复消费；顺带续 TTL 并返回剩余条数。
# 返回扁平数组 {id, remaining, field1, value1, ...}；空队列返回 nil。
_DEQUEUE_LUA = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local entries = redis.call('XRANGE', key, '-', '+', 'COUNT', 1)
if #entries == 0 then
    redis.call('EXPIRE', key, ttl)
    return nil
end
local id = entries[1][1]
local fields = entries[1][2]
redis.call('XDEL', key, id)
redis.call('EXPIRE', key, ttl)
local remaining = redis.call('XLEN', key)
local result = {id, remaining}
for i = 1, #fields do
    table.insert(result, fields[i])
end
return result
"""


def _queue_key(session_id: str) -> str:
    return _QUEUE_KEY.format(sid=session_id)


@dataclass(frozen=True)
class QueueItem:
    """一条队列消息；item_id 即 Redis Stream ID，created_at 为 ISO 字符串。"""

    item_id: str
    content: str
    position: int
    created_at: str
    provider: str | None
    model: str | None
    thinking_enabled: bool
    web_search_enabled: bool
    attachment_ids: list[str]
    mentions: list[dict[str, Any]]
    owner_uid: int | None


def _encode_item(
    *,
    content: str,
    provider: str | None,
    model: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    attachment_ids: list[str],
    mentions: list[dict[str, Any]],
    owner_uid: int | None,
) -> dict[str, str]:
    """把起回合所需参数编码成 Stream 字段；复杂结构 JSON 序列化。"""

    return {
        "content": content,
        "provider": provider or "",
        "model": model or "",
        "thinking_enabled": "1" if thinking_enabled else "0",
        "web_search_enabled": "1" if web_search_enabled else "0",
        "attachment_ids": json.dumps(attachment_ids, ensure_ascii=False),
        "mentions": json.dumps(mentions, ensure_ascii=False),
        "owner_uid": "" if owner_uid is None else str(owner_uid),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _decode_item(item_id: str, fields: dict[str, str], position: int) -> QueueItem:
    """Stream 字段还原成 QueueItem；容错缺失/损坏字段，不因脏数据中断消费链。"""

    def opt(key: str) -> str | None:
        value = fields.get(key, "")
        return value if value else None

    def json_list(key: str) -> list[Any]:
        raw = fields.get(key, "")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    owner_raw = fields.get("owner_uid", "")
    try:
        owner_uid = int(owner_raw) if owner_raw else None
    except ValueError:
        owner_uid = None

    return QueueItem(
        item_id=item_id,
        content=fields.get("content", ""),
        position=position,
        created_at=fields.get("created_at", ""),
        provider=opt("provider"),
        model=opt("model"),
        thinking_enabled=fields.get("thinking_enabled") == "1",
        web_search_enabled=fields.get("web_search_enabled") == "1",
        attachment_ids=[str(x) for x in json_list("attachment_ids")],
        mentions=[cast(dict[str, Any], x) for x in json_list("mentions") if isinstance(x, dict)],
        owner_uid=owner_uid,
    )


async def total(session_id: str) -> int:
    """队列剩余条数；顺带续 TTL。供通知 payload.total 使用。"""

    redis = get_redis()
    key = _queue_key(session_id)
    count = await redis.xlen(key)
    await redis.expire(key, _QUEUE_TTL)
    return count


async def enqueue(
    session_id: str,
    *,
    content: str,
    provider: str | None,
    model: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    attachment_ids: list[str],
    mentions: list[dict[str, Any]],
    owner_uid: int | None,
) -> tuple[str, int]:
    """入队一条消息，返回 (item_id, position)；position 为入队后队列长度。"""

    redis = get_redis()
    key = _queue_key(session_id)
    item_id = await redis.xadd(
        key,
        # redis-py 的字段类型用不变 TypeVar，str->str 的具体 dict 需 cast 才能匹配
        cast(dict[Any, Any], _encode_item(
            content=content,
            provider=provider,
            model=model,
            thinking_enabled=thinking_enabled,
            web_search_enabled=web_search_enabled,
            attachment_ids=attachment_ids,
            mentions=mentions,
            owner_uid=owner_uid,
        )),
    )
    position = await redis.xlen(key)
    await redis.expire(key, _QUEUE_TTL)
    logger.info("消息入队 session_id=%s item_id=%s position=%s", session_id, item_id, position)
    return item_id, position


async def list_items(session_id: str) -> list[QueueItem]:
    """按入队顺序列出全部消息；position 为 1 起的下标。"""

    redis = get_redis()
    key = _queue_key(session_id)
    rows = await redis.xrange(key, min="-", max="+")
    await redis.expire(key, _QUEUE_TTL)
    items = [
        _decode_item(stream_id, fields, position)
        for position, (stream_id, fields) in enumerate(rows, start=1)
    ]
    logger.info("队列列表 session_id=%s total=%s", session_id, len(items))
    return items


async def delete(session_id: str, item_id: str) -> bool:
    """按 item_id 删除一条；返回是否删到了。删空后 Stream 自动消失，EXPIRE 为 no-op。"""

    redis = get_redis()
    key = _queue_key(session_id)
    removed = await redis.xdel(key, item_id)
    await redis.expire(key, _QUEUE_TTL)
    logger.info("队列项删除 session_id=%s item_id=%s removed=%s", session_id, item_id, removed)
    return removed > 0


async def dequeue(session_id: str) -> QueueItem | None:
    """原子出队第一条（Lua 读 + XDEL）；队列空返回 None。"""

    redis = get_redis()
    key = _queue_key(session_id)
    # eval 的可变参数契约是 str，TTL 转成字符串传入；返回值实际是扁平数组，cast 绕过联合类型
    raw = await cast(
        Awaitable[Any],
        redis.eval(_DEQUEUE_LUA, 1, key, str(_QUEUE_TTL)),
    )
    if not raw:
        return None
    item_id = raw[0]
    fields = {raw[i]: raw[i + 1] for i in range(2, len(raw), 2)}
    item = _decode_item(item_id, fields, position=1)
    logger.info("队列出队 session_id=%s item_id=%s", session_id, item_id)
    return item
