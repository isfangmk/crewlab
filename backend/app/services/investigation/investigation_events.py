"""并行排查的事件总线。

一次排查的 worker 进度要能被多个订阅者接续看到：
右栏工作台断线重连、对话页与排查面板各连各的流，互不影响。

缓冲区负责「补发」：订阅方连上来时，先把它之前发生的事件推一遍，
再挂上队列接收后续事件，画面不会缺步骤。软截止后 worker 后台续跑的
事件也进同一条流，直到 dispatch_finished 才关闭通道、回收缓冲。

结构与 services/workflow/workflow_events.py 同款：按频道缓冲 + 多订阅者。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 排查结束后缓冲保留多久：够后到的订阅者补看，也不至于长期占内存
_RETENTION_SECONDS = 600
# 未结束通道的保留时长：后台任务异常退出时也要回收，不能永久驻留
_PENDING_RETENTION_SECONDS = 3600
# 同时保留的排查数量上限，按最后活动时间淘汰
_RETENTION_RUNS = 200
# 单次排查保留的事件条数上限，避免长日志把内存吃穿
_MAX_EVENTS_PER_RUN = 2000
# 单个订阅队列的容量：订阅方卡住不能把排查本身拖死
_SUBSCRIBER_QUEUE_SIZE = 1000

# 只有真正结束的排查才关闭通道；软截止返回后后台续跑仍要继续推事件
TERMINAL_EVENTS = ("dispatch_finished",)


class _RunChannel:
    """一次排查/计划执行的事件缓冲与订阅者。"""

    def __init__(self, dispatch_uid: str, terminal_events: tuple[str, ...]) -> None:
        self.dispatch_uid = dispatch_uid
        # 收尾事件集合按运行类型注入（排查 dispatch_finished / 计划 plan_finished）
        self.terminal_events = terminal_events
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS_PER_RUN)
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.finished_at: float | None = None
        self.last_active_at = time.time()

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def publish(self, event: dict[str, Any]) -> None:
        """记入缓冲并推给所有订阅者。

        队列满时丢最旧的一条而不是阻塞：订阅方卡住不能把排查本身拖死。
        """

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
                    logger.debug(
                        "事件订阅队列已满，丢弃一条 dispatch_uid=%s", self.dispatch_uid
                    )
        if event.get("event") in self.terminal_events:
            # 终态事件已经推给订阅方，随后才发收尾信号：
            # 收尾信号排在事件之后，订阅方读到的顺序才是「先事件、后断开」。
            self.finished_at = self.last_active_at
            self.close()

    def close(self) -> None:
        """通知所有订阅者收尾。"""

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self.subscribers.clear()


class InvestigationEventBus:
    """按 dispatch_uid 索引的排查事件总线。"""

    def __init__(self) -> None:
        self._channels: dict[str, _RunChannel] = {}

    def channel(
        self, dispatch_uid: str, terminal_events: tuple[str, ...] | None = None
    ) -> _RunChannel:
        channel = self._channels.get(dispatch_uid)
        if channel is None:
            channel = _RunChannel(
                dispatch_uid, terminal_events or TERMINAL_EVENTS
            )
            self._channels[dispatch_uid] = channel
        elif terminal_events is not None:
            # 先订阅后发布的场景：发布方注入的收尾事件集合为准
            channel.terminal_events = terminal_events
        return channel

    def open(
        self,
        dispatch_uid: str,
        *,
        terminal_events: tuple[str, ...] | None = None,
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """为一次运行返回事件发布函数，交给执行器与编排器。

        terminal_events 注入该类运行的收尾事件名（缺省排查口径）。
        """

        channel = self.channel(dispatch_uid, terminal_events)
        if channel.finished:
            # 同一 uid 重开运行（重试/跳过后恢复执行）：复位生命周期但保留缓冲，
            # 否则新订阅方会被 is_finished 立即判终态、收不到续跑事件
            channel.finished_at = None

        async def publish(event: dict[str, Any]) -> None:
            channel.publish(event)
            self._sweep()

        return publish

    async def subscribe(
        self, dispatch_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """订阅：返回（已发生的事件，待接收的队列）。

        先挂队列再取缓冲，保证「补发」与「后续」之间不漏事件。
        """

        channel = self.channel(dispatch_uid)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_SIZE
        )
        channel.subscribers.add(queue)
        channel.last_active_at = time.time()
        self._sweep()
        return list(channel.events), queue

    def unsubscribe(
        self, dispatch_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        channel = self._channels.get(dispatch_uid)
        if channel is not None:
            channel.subscribers.discard(queue)
            channel.last_active_at = time.time()

    def is_finished(self, dispatch_uid: str) -> bool:
        channel = self._channels.get(dispatch_uid)
        return channel is not None and channel.finished

    def _sweep(self) -> None:
        """回收排查通道，避免长连接把内存吃穿。"""

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
            ((channel.last_active_at, uid) for uid, channel in self._channels.items()),
        )
        for _last_active_at, uid in remaining[: max(0, len(remaining) - _RETENTION_RUNS)]:
            self._channels.pop(uid, None)


# 进程内单例：当前是单进程部署，广播走内存即可。
# 多 worker 部署时需要换成跨进程的发布订阅，接口不变。
investigation_events = InvestigationEventBus()
