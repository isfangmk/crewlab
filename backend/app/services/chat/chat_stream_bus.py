"""对话流帧总线：按回合用 Redis Stream 分发帧，支持多订阅者补发+续推。

设计要点（阶段1）：
- 生产只有一份：后台任务消费 stream_session_message（含 persister 落库），逐帧
  publish 到本回合的 Stream；所有订阅者只读流，不重复落库。
- 一个回合一个 Stream（chat:turn:{turn_id}）：天然隔离历史回合，订阅者只读当前
  turn，不会收到旧回合的帧。活跃登记 chat:active:{sid} 指向当前 turn_id。
- Stream ID 即全局单调 seq：XRANGE 补发已发生帧，XREAD BLOCK 续推新帧。
- done/error 是终态帧：订阅者读到即停止；帧本身也在流里，后到者能看到完整回合。

key 约定：
  chat:turn:{turn_id}  —— Stream，帧字段含 event/data
  chat:active:{sid}    —— Hash，turn_id/started_at/status，指向当前回合
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from typing import Any, cast

from app.redis import get_redis

logger = logging.getLogger(__name__)

_TURN_KEY = "chat:turn:{turn_id}"
_ACTIVE_KEY = "chat:active:{sid}"
# 单回合保留帧数上限：够一次长回合补发，又不让 Stream 无限增长
_MAXLEN = 5000
# 回合 Stream TTL（秒）：回合结束后保留供后到订阅者补看，超时自动回收
_TURN_TTL = 1800
# 活跃回合登记 TTL（秒）：后台任务崩溃时自动过期，避免永久"进行中"
_ACTIVE_TTL = 3600
# XREAD 阻塞时长（毫秒）
_BLOCK_MS = 1000


def _turn_key(turn_id: str) -> str:
    return _TURN_KEY.format(turn_id=turn_id)


def _active_key(session_id: str) -> str:
    return _ACTIVE_KEY.format(sid=session_id)


async def start_turn(session_id: str) -> str:
    """登记一个进行中回合，返回 turn_id。新一轮覆盖上一轮登记。"""

    turn_id = str(uuid.uuid4())
    redis = get_redis()
    await cast(
        Awaitable[int],
        redis.hset(
            _active_key(session_id),
            mapping={
                "turn_id": turn_id,
                "started_at": str(int(time.time())),
                "status": "running",
            },
        ),
    )
    await cast(Awaitable[bool], redis.expire(_active_key(session_id), _ACTIVE_TTL))
    logger.info("对话回合登记 session_id=%s turn_id=%s", session_id, turn_id)
    return turn_id


async def publish(turn_id: str, *, event: str, data: dict[str, Any]) -> str:
    """发布一帧到本回合 Stream，返回 Stream ID（即 seq）。"""

    redis = get_redis()
    key = _turn_key(turn_id)
    stream_id = await redis.xadd(
        key,
        {"event": event, "data": json.dumps(data, ensure_ascii=False)},
        maxlen=_MAXLEN,
        approximate=True,
    )
    # 首帧起设 TTL：整条 Stream 在结束后一段时间自动回收，无需显式删除
    await redis.expire(key, _TURN_TTL)
    return stream_id


async def finish_turn(session_id: str, *, status: str = "finished") -> None:
    """标记回合结束（帧已由 publish 写入，这里只更新登记状态）。"""

    redis = get_redis()
    key = _active_key(session_id)
    # 用短 TTL 保留一会儿，供刚到的订阅者判断"回合已结束"
    await cast(Awaitable[int], redis.hset(key, "status", status))
    await cast(Awaitable[bool], redis.expire(key, 60))


async def active_turn(session_id: str) -> dict[str, str] | None:
    """查活跃回合登记；无登记或已结束返回 None。"""

    redis = get_redis()
    raw = await cast(Awaitable[dict[str, str]], redis.hgetall(_active_key(session_id)))
    if not raw or raw.get("status") not in (None, "running"):
        return None
    return raw


TERMINAL_EVENTS = frozenset({"done", "error"})


async def subscribe(
    turn_id: str, *, last_id: str = "0-0"
) -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
    """订阅某回合的帧流：先 XRANGE 补发 last_id 之后的帧，再 XREAD BLOCK 续推。

    yielded: (stream_id, event, data)。读到终态帧（done/error）即结束迭代。
    连接断开由调用方取消本生成器即可；不持有服务端订阅状态（Stream 本身常驻）。
    """

    redis = get_redis()
    key = _turn_key(turn_id)
    cursor = last_id
    # 第一段：补发历史。last_id=0-0 时用 "-"（含全部）；"(0-0" 是非法 Stream ID。
    start = "-" if last_id == "0-0" else f"({last_id}"
    backlog = await redis.xrange(key, min=start, max="+")
    for stream_id, fields in backlog:
        cursor = stream_id
        event = fields.get("event", "")
        try:
            data = json.loads(fields.get("data", "{}"))
        except json.JSONDecodeError:
            data = {}
        yield stream_id, event, data
        if event in TERMINAL_EVENTS:
            return

    # 第二段：续推新帧。XREAD BLOCK 无新帧返回 None，循环继续（可被取消）。
    while True:
        result = await redis.xread({key: cursor}, block=_BLOCK_MS, count=100)
        if not result:
            continue
        _stream, messages = result[0]
        for stream_id, fields in messages:
            cursor = stream_id
            event = fields.get("event", "")
            try:
                data = json.loads(fields.get("data", "{}"))
            except json.JSONDecodeError:
                data = {}
            yield stream_id, event, data
            if event in TERMINAL_EVENTS:
                return


async def drop_stream(turn_id: str) -> None:
    """删除某回合帧流（正常不用，靠 TTL 回收；仅显式清理时调用）。"""

    await get_redis().delete(_turn_key(turn_id))
