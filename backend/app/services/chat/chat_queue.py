"""对话消息队列：按会话用 Redis Stream 缓冲「回答进行中发送」的消息。

回答还在生成时用户再发的消息默认排队（可多条、可删除），当前回合结束后服务端
按入队顺序依次消费，形成链式回合直到队列空。队列项支持原地改正文与标记插话。

设计要点：
- 顺序用 Stream `chat:queue:{sid}`：天然按 sid 隔离、XRANGE 按入队顺序返回；
  Stream 只存一个字段 iid（条目 UUID），**不存可变内容**。
- 可变内容存 Hash `chat:queue:data:{sid}`：iid → JSON（正文/模型/开关/附件等）。
  这样「改正文 / 标记插话」只改 Hash，不动 Stream，位置严格不变。
  （Redis Stream 的 ID 单调递增、删后不可复用，若把正文放 Stream 里就无法原地改。）
- 插话标记存 Hash `chat:queue:int:{sid}`：iid → "1"，出队 Lua 先扫出带标记的项
  优先出队，实现「插话插队优先」。
- 出队用 Lua（Stream 删条目 + 两个 Hash 删字段）原子完成，防多 worker 重复消费。
- 每次操作都续 TTL（24h）：队列是临时缓冲，会话长期不活跃自动回收。

key 约定与注释风格对齐 chat_stream_bus.py：
  chat:queue:{sid}       —— Stream，字段仅 iid
  chat:queue:data:{sid}  —— Hash，iid → JSON 字段
  chat:queue:int:{sid}   —— Hash，iid → "1"（仅插话项）
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.redis import get_redis

logger = logging.getLogger(__name__)

_ORDER_KEY = "chat:queue:{sid}"
_DATA_KEY = "chat:queue:data:{sid}"
_INT_KEY = "chat:queue:int:{sid}"
# 队列 TTL（秒）：24h。回答中排队的消息是临时缓冲，长期不活跃自动回收
_QUEUE_TTL = 24 * 3600

# 原子出队：优先取插话标记项，否则第一条；Stream XDEL + 两个 Hash HDEL + 续 TTL。
# 返回扁平数组 {stream_id, remaining, iid, field1, value1, ...}；空队列返回 nil。
# Lua 内用 HGET int_key iid 判断插话标记，避免解析 JSON。
_DEQUEUE_LUA = """
local order = KEYS[1]
local data = KEYS[2]
local ints = KEYS[3]
local ttl = tonumber(ARGV[1])
local entries = redis.call('XRANGE', order, '-', '+')
if #entries == 0 then
    redis.call('EXPIRE', order, ttl)
    redis.call('EXPIRE', data, ttl)
    redis.call('EXPIRE', ints, ttl)
    return nil
end
local chosen = 1
for i, entry in ipairs(entries) do
    local iid = entry[2][2]
    if redis.call('HGET', ints, iid) == '1' then
        chosen = i
        break
    end
end
local stream_id = entries[chosen][1]
local iid = entries[chosen][2][2]
local raw = redis.call('HGET', data, iid)
redis.call('XDEL', order, stream_id)
redis.call('HDEL', data, iid)
redis.call('HDEL', ints, iid)
redis.call('EXPIRE', order, ttl)
redis.call('EXPIRE', data, ttl)
redis.call('EXPIRE', ints, ttl)
local remaining = redis.call('XLEN', order)
local result = {stream_id, remaining, iid}
if raw then
    table.insert(result, 'json')
    table.insert(result, raw)
end
return result
"""


def _order_key(session_id: str) -> str:
    return _ORDER_KEY.format(sid=session_id)


def _data_key(session_id: str) -> str:
    return _DATA_KEY.format(sid=session_id)


def _int_key(session_id: str) -> str:
    return _INT_KEY.format(sid=session_id)


@dataclass(frozen=True)
class QueueItem:
    """一条队列消息；item_id 是入队时生成的 UUID，created_at 为 ISO 字符串。"""

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
    interrupt: bool = False


def _encode_item(item_id: str, payload: dict[str, Any]) -> str:
    """把一条队列项序列化成 JSON 存 Hash；复杂字段原样保留。"""
    return json.dumps({"item_id": item_id, **payload}, ensure_ascii=False)


def _decode_item(stored: str, position: int) -> QueueItem | None:
    """Hash JSON 还原成 QueueItem；容错损坏字段，不因脏数据中断消费链。"""
    try:
        raw = json.loads(stored)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    def opt(key: str) -> str | None:
        value = raw.get(key, "")
        return value if isinstance(value, str) and value else None

    def json_list(key: str) -> list[Any]:
        value = raw.get(key, [])
        return value if isinstance(value, list) else []

    owner_raw = raw.get("owner_uid")
    try:
        owner_uid = int(owner_raw) if owner_raw not in (None, "") else None
    except (ValueError, TypeError):
        owner_uid = None

    return QueueItem(
        item_id=str(raw.get("item_id", "")),
        content=str(raw.get("content", "")),
        position=position,
        created_at=str(raw.get("created_at", "")),
        provider=opt("provider"),
        model=opt("model"),
        thinking_enabled=bool(raw.get("thinking_enabled")),
        web_search_enabled=bool(raw.get("web_search_enabled")),
        attachment_ids=[str(x) for x in json_list("attachment_ids")],
        mentions=[cast(dict[str, Any], x) for x in json_list("mentions") if isinstance(x, dict)],
        owner_uid=owner_uid,
        interrupt=bool(raw.get("interrupt")),
    )


async def _renew_ttl(session_id: str) -> None:
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.expire(_order_key(session_id), _QUEUE_TTL)
    pipe.expire(_data_key(session_id), _QUEUE_TTL)
    pipe.expire(_int_key(session_id), _QUEUE_TTL)
    await cast(Awaitable[Any], pipe.execute())


async def total(session_id: str) -> int:
    """队列剩余条数；顺带续 TTL。供通知 payload.total 使用。"""

    redis = get_redis()
    count = await redis.xlen(_order_key(session_id))
    await _renew_ttl(session_id)
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
    item_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "content": content,
        "provider": provider or "",
        "model": model or "",
        "thinking_enabled": thinking_enabled,
        "web_search_enabled": web_search_enabled,
        "attachment_ids": attachment_ids,
        "mentions": mentions,
        "owner_uid": owner_uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "interrupt": False,
    }
    pipe = redis.pipeline()
    pipe.hset(_data_key(session_id), item_id, _encode_item(item_id, payload))
    pipe.xadd(_order_key(session_id), {"iid": item_id})
    await cast(Awaitable[Any], pipe.execute())
    await _renew_ttl(session_id)
    position = await redis.xlen(_order_key(session_id))
    logger.info("消息入队 session_id=%s item_id=%s position=%s", session_id, item_id, position)
    return item_id, position


async def list_items(session_id: str) -> list[QueueItem]:
    """按入队顺序列出全部消息；position 为 1 起的下标。"""

    redis = get_redis()
    rows = await redis.xrange(_order_key(session_id), min="-", max="+")
    iids = [fields.get("iid", "") for _, fields in rows]
    stored = (
        await cast(Awaitable[list[Any]], redis.hmget(_data_key(session_id), *iids))
        if iids
        else []
    )
    items: list[QueueItem] = []
    for position, raw in enumerate(stored, start=1):
        if raw is None:
            continue
        decoded = _decode_item(raw, position) if isinstance(raw, str) else None
        if decoded is not None:
            items.append(decoded)
    await _renew_ttl(session_id)
    logger.info("队列列表 session_id=%s total=%s", session_id, len(items))
    return items


async def delete(session_id: str, item_id: str) -> bool:
    """按 item_id 删除一条；返回是否删到了。"""

    redis = get_redis()
    order_key = _order_key(session_id)
    rows = await redis.xrange(order_key, min="-", max="+")
    target_id = next(
        (stream_id for stream_id, fields in rows if fields.get("iid") == item_id), None
    )
    if target_id is None:
        return False
    pipe = redis.pipeline()
    pipe.xdel(order_key, target_id)
    pipe.hdel(_data_key(session_id), item_id)
    pipe.hdel(_int_key(session_id), item_id)
    await cast(Awaitable[Any], pipe.execute())
    await _renew_ttl(session_id)
    logger.info("队列项删除 session_id=%s item_id=%s removed=1", session_id, item_id)
    return True


async def update_content(session_id: str, item_id: str, content: str) -> bool:
    """原地改队列项正文：只改 Hash，Stream 顺序与位置不变。"""

    redis = get_redis()
    data_key = _data_key(session_id)
    raw = await cast(Awaitable[str | None], redis.hget(data_key, item_id))
    if raw is None:
        return False
    item = _decode_item(raw, position=1)
    if item is None:
        return False
    payload = {
        "content": content,
        "provider": item.provider or "",
        "model": item.model or "",
        "thinking_enabled": item.thinking_enabled,
        "web_search_enabled": item.web_search_enabled,
        "attachment_ids": item.attachment_ids,
        "mentions": item.mentions,
        "owner_uid": item.owner_uid,
        "created_at": item.created_at,
        "interrupt": item.interrupt,
    }
    await cast(Awaitable[int], redis.hset(data_key, item_id, _encode_item(item_id, payload)))
    await _renew_ttl(session_id)
    logger.info("队列项改正文 session_id=%s item_id=%s", session_id, item_id)
    return True


async def mark_interrupt(session_id: str, item_id: str, value: bool = True) -> bool:
    """标记/取消队列项插话：只改 Hash（int 索引 + data 内字段），位置不变。"""

    redis = get_redis()
    data_key = _data_key(session_id)
    raw = await cast(Awaitable[str | None], redis.hget(data_key, item_id))
    if raw is None:
        return False
    item = _decode_item(raw, position=1)
    if item is None:
        return False
    payload = {
        "content": item.content,
        "provider": item.provider or "",
        "model": item.model or "",
        "thinking_enabled": item.thinking_enabled,
        "web_search_enabled": item.web_search_enabled,
        "attachment_ids": item.attachment_ids,
        "mentions": item.mentions,
        "owner_uid": item.owner_uid,
        "created_at": item.created_at,
        "interrupt": value,
    }
    pipe = redis.pipeline()
    pipe.hset(data_key, item_id, _encode_item(item_id, payload))
    if value:
        pipe.hset(_int_key(session_id), item_id, "1")
    else:
        pipe.hdel(_int_key(session_id), item_id)
    await cast(Awaitable[Any], pipe.execute())
    await _renew_ttl(session_id)
    logger.info("队列项插话标记 session_id=%s item_id=%s value=%s", session_id, item_id, value)
    return True


async def dequeue(session_id: str) -> QueueItem | None:
    """原子出队第一条（优先插话项：Lua Stream XDEL + Hash HDEL）；队列空返回 None。"""

    redis = get_redis()
    raw = await cast(
        Awaitable[Any],
        redis.eval(
            _DEQUEUE_LUA,
            3,
            _order_key(session_id),
            _data_key(session_id),
            _int_key(session_id),
            str(_QUEUE_TTL),
        ),
    )
    if not raw:
        return None
    # Lua 返回扁平数组 1-based：{stream_id, remaining, iid, 'json', raw_json}
    # redis-py 转 0-based：raw[3]='json' 标记，raw[4] 才是正文 JSON
    position = 1
    item = _decode_item(raw[4], position) if len(raw) > 4 else None
    if item is None:
        return None
    logger.info(
        "队列出队 session_id=%s item_id=%s interrupt=%s", session_id, item.item_id, item.interrupt
    )
    return item
