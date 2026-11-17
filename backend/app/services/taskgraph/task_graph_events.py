"""任务图事件总线：按 graph_uid 缓冲 + 多订阅者，支持断线重连补发。

一次图运行的节点进度要能被多个订阅者接续看到（右栏工作台重连、对话页各连各的流）。
订阅方连上来时先补发已发生事件，再挂队列收后续事件，画面不缺步骤。
终态 graph_finished 关闭通道、回收缓冲。结构与旧 investigation_events 同款，
但独立成新文件、新命名，不复用旧模块。

单进程部署下走内存即可；多 worker 部署时需换跨进程发布订阅，接口不变。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_RETENTION_SECONDS = 600
_PENDING_RETENTION_SECONDS = 3600
_RETENTION_RUNS = 200
_MAX_EVENTS_PER_RUN = 2000
_SUBSCRIBER_QUEUE_SIZE = 1000

# 只有终态事件才关闭通道；汇总 delta 等后续事件仍在同一条流里。
TERMINAL_EVENTS = ("graph_finished",)


class _GraphChannel:
    def __init__(self, graph_uid: str) -> None:
        self.graph_uid = graph_uid
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS_PER_RUN)
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.finished_at: float | None = None
        self.last_active_at = time.time()

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.last_active_at = time.time()
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.debug("事件订阅队列已满，丢弃一条 graph_uid=%s", self.graph_uid)
        if event.get("event") in TERMINAL_EVENTS:
            self.finished_at = self.last_active_at
            self.close()

    def close(self) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self.subscribers.clear()


class TaskGraphEventBus:
    def __init__(self) -> None:
        self._channels: dict[str, _GraphChannel] = {}

    def open(self, graph_uid: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """为一次运行返回事件发布函数；重开时复位生命周期但保留缓冲。"""

        channel = self._channels.get(graph_uid)
        if channel is None:
            channel = _GraphChannel(graph_uid)
            self._channels[graph_uid] = channel
        elif channel.finished:
            channel.finished_at = None

        async def publish(event: dict[str, Any]) -> None:
            channel.publish(event)
            self._sweep()

        return publish

    async def subscribe(
        self, graph_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """先挂队列再取缓冲，保证补发与后续之间不漏事件。"""

        channel = self._channels.get(graph_uid)
        if channel is None:
            channel = _GraphChannel(graph_uid)
            self._channels[graph_uid] = channel
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_SIZE
        )
        channel.subscribers.add(queue)
        channel.last_active_at = time.time()
        self._sweep()
        return list(channel.events), queue

    def unsubscribe(
        self, graph_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        channel = self._channels.get(graph_uid)
        if channel is not None:
            channel.subscribers.discard(queue)
            channel.last_active_at = time.time()

    def is_finished(self, graph_uid: str) -> bool:
        channel = self._channels.get(graph_uid)
        return channel is not None and channel.finished

    def _sweep(self) -> None:
        now = time.time()
        expired = [
            uid
            for uid, channel in self._channels.items()
            if (
                channel.finished_at is not None
                and now - channel.finished_at > _RETENTION_SECONDS
            )
            or (
                channel.finished_at is None
                and now - channel.last_active_at > _PENDING_RETENTION_SECONDS
            )
        ]
        for uid in expired:
            self._channels.pop(uid, None)
        remaining = sorted(
            (channel.last_active_at, uid) for uid, channel in self._channels.items()
        )
        for _last_active_at, uid in remaining[: max(0, len(remaining) - _RETENTION_RUNS)]:
            self._channels.pop(uid, None)


task_graph_events = TaskGraphEventBus()
