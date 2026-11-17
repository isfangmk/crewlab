"""全局通知总线：按用户 uid 用 Redis Stream 分发状态变更通知。

为什么需要：浏览器各标签页靠 5 秒轮询发现「别的标签页/别的 worker」做的状态变更
（消息落库、审批决定、任务图收尾、外部源同步完成、评测跑完）。轮询既慢又浪费请求。
本模块让后端在状态流转处发一条通知，所有标签页通过一个 SSE 连接即时收到。

设计（复用 app.services.eventbus.redis_stream 的成熟语义）：
- 每个 uid 一条 Stream（notify:{uid}），XADD 写通知，XREAD BLOCK 续推；
- Stream ID 即单调 seq，SSE 用 id 字段支持 Last-Event-ID 断线续传；
- 通知是「信号」不是「数据」：payload 只带变化标识（如 session_id / kind），
  前端收到后重拉权威数据，避免把大对象塞进 Redis 或与 DB 口径不一致；
- MAXLEN + TTL 回收，通知不永久堆积。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.redis import get_redis

logger = logging.getLogger(__name__)

_KEY = "notify:{uid}"
_MAXLEN = 500
_TTL = 3600
_BLOCK_MS = 1000


def _key(uid: str) -> str:
    return _KEY.format(uid=uid)


async def publish(uid: str | int | None, *, kind: str, payload: dict[str, Any] | None = None) -> None:
    """发一条通知给某用户的所有标签页；uid 为空则无人订阅，安全跳过。"""

    if uid is None:
        return
    redis = get_redis()
    key = _key(str(uid))
    stream_id = await redis.xadd(
        key,
        {
            "kind": kind,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
        maxlen=_MAXLEN,
        approximate=False,
    )
    await redis.expire(key, _TTL)
    logger.info("通知已发布 uid=%s kind=%s id=%s", uid, kind, stream_id)


async def subscribe(uid: str | int, *, last_id: str = "0-0") -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
    """订阅某用户的通知流：(stream_id, kind, payload)；供 SSE 端点消费。

    last_id=0-0 从全部历史开始（新连接补发未读通知）；断点续传传上次的 id。
    """

    redis = get_redis()
    key = _key(str(uid))
    cursor = last_id
    start = "-" if last_id == "0-0" else f"({last_id}"
    backlog = await redis.xrange(key, min=start, max="+")
    for stream_id, fields in backlog:
        cursor = stream_id
        yield stream_id, fields.get("kind", ""), _decode(fields)
    while True:
        result = await redis.xread({key: cursor}, block=_BLOCK_MS, count=100)
        if not result:
            continue
        _stream, messages = result[0]
        for stream_id, fields in messages:
            cursor = stream_id
            yield stream_id, fields.get("kind", ""), _decode(fields)


def _decode(fields: dict[str, str]) -> dict[str, Any]:
    try:
        return json.loads(fields.get("payload", "{}"))
    except json.JSONDecodeError:
        return {}
