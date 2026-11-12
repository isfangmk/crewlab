"""工作流运行的事件总线。

一次运行只跑一次，事件要能被多个订阅者接续看到：
发起方（列表页 / 编排页）和查看方（运行详情页）各连各的流，
任一方断开都不影响运行本身，也不影响另一方。

缓冲区负责「补发」：订阅方连上来时，先把它之前发生的事件推一遍，
再挂上队列接收后续事件，画面不会缺步骤。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# 运行结束后缓冲保留多久：够后到的订阅者补看，也不至于长期占内存
_RETENTION_SECONDS = 600
# 同时保留的已结束运行数量上限，按先结束先淘汰
_RETENTION_RUNS = 200
# 单次运行保留的事件条数上限，避免长循环把内存吃穿
_MAX_EVENTS_PER_RUN = 2000

TERMINAL_EVENTS = ("run_finished", "run_failed")


class _RunChannel:
    """一次运行的事件缓冲与订阅者。"""

    def __init__(self, run_uid: str) -> None:
        self.run_uid = run_uid
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS_PER_RUN)
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.finished_at: float | None = None

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def publish(self, event: dict[str, Any]) -> None:
        """记入缓冲并推给所有订阅者。

        队列满时丢最旧的一条而不是阻塞：订阅方卡住不能把运行本身拖死。
        """

        self.events.append(event)
        if event.get("event") in TERMINAL_EVENTS:
            self.finished_at = time.time()
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.debug("事件订阅队列已满，丢弃一条 run_uid=%s", self.run_uid)

    def close(self) -> None:
        """通知所有订阅者收尾。"""

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self.subscribers.clear()


class WorkflowEventBus:
    """按 run_uid 索引的运行事件总线。"""

    def __init__(self) -> None:
        self._channels: dict[str, _RunChannel] = {}

    def channel(self, run_uid: str) -> _RunChannel:
        channel = self._channels.get(run_uid)
        if channel is None:
            channel = _RunChannel(run_uid)
            self._channels[run_uid] = channel
        return channel

    def open(self, run_uid: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """为一次运行返回事件发布函数，交给 execute_run 的 on_event。"""

        channel = self.channel(run_uid)

        async def publish(event: dict[str, Any]) -> None:
            channel.publish(event)
            if channel.finished:
                self._sweep()

        return publish

    async def subscribe(
        self, run_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """订阅：返回（已发生的事件，待接收的队列）。

        先挂队列再取缓冲，保证「补发」与「后续」之间不漏事件。
        """

        channel = self.channel(run_uid)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)
        channel.subscribers.add(queue)
        return list(channel.events), queue

    def unsubscribe(
        self, run_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        channel = self._channels.get(run_uid)
        if channel is not None:
            channel.subscribers.discard(queue)

    def is_finished(self, run_uid: str) -> bool:
        channel = self._channels.get(run_uid)
        return channel is not None and channel.finished

    def _sweep(self) -> None:
        """按保留时长与数量回收已结束的运行。"""

        now = time.time()
        expired = [
            uid
            for uid, channel in self._channels.items()
            if channel.finished_at is not None
            and now - channel.finished_at > _RETENTION_SECONDS
        ]
        for uid in expired:
            self._channels.pop(uid, None)

        finished = sorted(
            (
                (channel.finished_at, uid)
                for uid, channel in self._channels.items()
                if channel.finished_at is not None
            ),
        )
        for _finished_at, uid in finished[: max(0, len(finished) - _RETENTION_RUNS)]:
            self._channels.pop(uid, None)


# 进程内单例：当前是单进程部署，广播走内存即可。
# 多 worker 部署时需要换成跨进程的发布订阅，接口不变。
workflow_events = WorkflowEventBus()
