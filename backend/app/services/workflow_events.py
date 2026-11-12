"""工作流运行的事件总线。

一次运行只跑一次，事件要能被多个订阅者接续看到：
发起方（列表页 / 编排页）和查看方（运行详情页）各连各的流，
任一方断开都不影响运行本身，也不影响另一方。

缓冲区负责「补发」：订阅方连上来时，先把它之前发生的事件推一遍，
再挂上队列接收后续事件，画面不会缺步骤。

运行停在人工节点时只是暂停，通道保持打开；人工确认后的续跑事件仍进同一条流，
直到 run_finished 才关闭通道、回收缓冲。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 运行结束后缓冲保留多久：够后到的订阅者补看，也不至于长期占内存
_RETENTION_SECONDS = 600
# 未结束通道的保留时长：后台任务异常退出时也要回收，不能永久驻留
_PENDING_RETENTION_SECONDS = 3600
# 同时保留的运行数量上限，按最后活动时间淘汰
_RETENTION_RUNS = 200
# 单次运行保留的事件条数上限，避免长循环把内存吃穿
_MAX_EVENTS_PER_RUN = 2000
# 单个订阅队列的容量：订阅方卡住不能把运行本身拖死
_SUBSCRIBER_QUEUE_SIZE = 1000

# 只有真正结束的运行才关闭通道；等待人工的运行还要继续推续跑事件
TERMINAL_EVENTS = ("run_finished",)


class _RunChannel:
    """一次运行的事件缓冲与订阅者。"""

    def __init__(self, run_uid: str) -> None:
        self.run_uid = run_uid
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS_PER_RUN)
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.finished_at: float | None = None
        self.last_active_at = time.time()

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def publish(self, event: dict[str, Any]) -> None:
        """记入缓冲并推给所有订阅者。

        队列满时丢最旧的一条而不是阻塞：订阅方卡住不能把运行本身拖死。
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
                    logger.debug("事件订阅队列已满，丢弃一条 run_uid=%s", self.run_uid)
        if event.get("event") in TERMINAL_EVENTS:
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
        """为一次运行返回事件发布函数，交给运行服务的 on_event。"""

        channel = self.channel(run_uid)

        async def publish(event: dict[str, Any]) -> None:
            channel.publish(event)
            self._sweep()

        return publish

    async def subscribe(
        self, run_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """订阅：返回（已发生的事件，待接收的队列）。

        先挂队列再取缓冲，保证「补发」与「后续」之间不漏事件。
        """

        channel = self.channel(run_uid)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_SIZE
        )
        channel.subscribers.add(queue)
        channel.last_active_at = time.time()
        self._sweep()
        return list(channel.events), queue

    def unsubscribe(
        self, run_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        channel = self._channels.get(run_uid)
        if channel is not None:
            channel.subscribers.discard(queue)
            channel.last_active_at = time.time()

    def is_finished(self, run_uid: str) -> bool:
        channel = self._channels.get(run_uid)
        return channel is not None and channel.finished

    def _sweep(self) -> None:
        """回收运行通道，避免长连接把内存吃穿。

        已结束的运行按终态时间淘汰；未结束的运行按最后活动时间兜底淘汰，
        防止后台任务异常退出后通道永久驻留。
        """

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
workflow_events = WorkflowEventBus()
