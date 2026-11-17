"""Redis 事件总线 transport：Stream + 本地队列桥接，供多 worker 跨进程广播。

对外语义与进程内版完全一致（workflow_events / task_graph_events 不改公开接口）：
- publish(uid, event)  → XADD 到该运行/图的 Stream；终态事件额外写 finished 标记。
- subscribe(uid)       → (backlog, asyncio.Queue)，先补发已发生事件，再 XREAD 续推；
                          后台读任务把新事件写进 queue，None 表示收尾。
- unsubscribe(uid,q)   → 取消该订阅者的读任务。
- is_finished(uid)     → 查 finished 标记。

用 Redis Stream 的天然多消费者语义：每个订阅者独立游标（不从 Stream 删除消息），
因此多个 worker / 多个 SSE 连接各读各的，互不抢事件。Stream 靠 MAXLEN + TTL 回收。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.redis import get_redis

logger = logging.getLogger(__name__)

_STREAM_KEY = "evt:{kind}:{uid}"
_FINISHED_KEY = "evt:{kind}:{uid}:finished"
# 单运行/图保留事件上限与进程内版一致
_MAXLEN = 2000
# finished 标记 TTL：与进程内 600s 保留口径一致
_FINISHED_TTL = 600
# Stream TTL：未结束的运行靠它兜底回收（对齐进程内 3600s pending 保留）
_STREAM_TTL = 3600
_BLOCK_MS = 1000


class RedisEventStream:
    """一种事件域（workflow / task_graph）的 Redis Stream transport。"""

    def __init__(self, kind: str, terminal_events: tuple[str, ...]) -> None:
        self._kind = kind
        self._terminal = frozenset(terminal_events)
        # 订阅者的读任务：queue ->-task，unsubscribe 时精确取消
        self._readers: dict[asyncio.Queue[dict[str, Any] | None], asyncio.Task[None]] = {}
        # 终态集合：publish 或读到终态事件时置位。公开 is_finished 保持同步（SSE 端点不 await）。
        # 多 worker 下订阅者必从 Stream 读到终态事件（backlog 或 XREAD），读到即置位，因此正确。
        self._finished: set[str] = set()

    def _stream_key(self, uid: str) -> str:
        return _STREAM_KEY.format(kind=self._kind, uid=uid)

    def _finished_key(self, uid: str) -> str:
        return _FINISHED_KEY.format(kind=self._kind, uid=uid)

    async def publish(self, uid: str, event: dict[str, Any]) -> None:
        redis = get_redis()
        key = self._stream_key(uid)
        await redis.xadd(
            key,
            {"event": json.dumps(event, ensure_ascii=False)},
            maxlen=_MAXLEN,
            approximate=True,
        )
        await redis.expire(key, _STREAM_TTL)
        if event.get("event") in self._terminal:
            self._finished.add(uid)
            await redis.set(self._finished_key(uid), "1", ex=_FINISHED_TTL)
            logger.info("事件流终态 kind=%s uid=%s event=%s", self._kind, uid, event.get("event"))

    async def subscribe(
        self, uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        redis = get_redis()
        key = self._stream_key(uid)
        # 补发：读当前全部事件
        backlog_raw = await redis.xrange(key, min="-", max="+")
        backlog = [self._decode(fields) for _id, fields in backlog_raw]
        cursor = backlog_raw[-1][0] if backlog_raw else "0-0"
        # backlog 里已有终态事件：置位，供端点补发后直接收尾
        if any(e.get("event") in self._terminal for e in backlog):
            self._finished.add(uid)

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)
        reader = asyncio.create_task(self._read_loop(uid, key, cursor, queue))
        self._readers[queue] = reader
        return backlog, queue

    async def _read_loop(
        self,
        uid: str,
        key: str,
        cursor: str,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        """XREAD BLOCK 续推：新事件写 queue；终态后写 None 收尾。"""

        redis = get_redis()
        try:
            while True:
                result = await redis.xread({key: cursor}, block=_BLOCK_MS, count=100)
                if not result:
                    continue
                _stream, messages = result[0]
                terminal = False
                for stream_id, fields in messages:
                    cursor = stream_id
                    event = self._decode(fields)
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        # 订阅方卡住：丢最旧一条，不阻塞事件生产（对齐进程内口径）
                        try:
                            queue.get_nowait()
                            queue.put_nowait(event)
                        except (asyncio.QueueEmpty, asyncio.QueueFull):
                            pass
                    if event.get("event") in self._terminal:
                        terminal = True
                if terminal:
                    self._finished.add(uid)
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 读循环异常写 None 让 SSE 端点收尾，不悬空
            logger.exception("事件流读取失败 kind=%s uid=%s", self._kind, uid)
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def unsubscribe(
        self, uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        reader = self._readers.pop(queue, None)
        if reader is not None and not reader.done():
            reader.cancel()

    def is_finished(self, uid: str) -> bool:
        """同步判断（公开接口不变）；终态由 publish 或读循环置位。"""

        return uid in self._finished

    async def drop(self, uid: str) -> None:
        """显式清理（正常靠 TTL；供测试/会话删除时用）。"""

        redis = get_redis()
        await redis.delete(self._stream_key(uid), self._finished_key(uid))

    @staticmethod
    def _decode(fields: dict[str, str]) -> dict[str, Any]:
        raw = fields.get("event", "{}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
