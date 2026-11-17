"""工作流运行的事件总线。

一次运行只跑一次，事件要能被多个订阅者接续看到：
发起方（列表页 / 编排页）和查看方（运行详情页）各连各的流，
任一方断开都不影响运行本身，也不影响另一方。

缓冲区负责「补发」：订阅方连上来时，先把它之前发生的事件推一遍，
再挂上队列接收后续事件，画面不会缺步骤。

运行停在人工节点时只是暂停，通道保持打开；人工确认后的续跑事件仍进同一条流，
直到 run_finished 才关闭通道、回收缓冲。

多 worker 部署：底层用 Redis Stream 跨进程广播（app.services.eventbus），
公开接口 publish/subscribe/unsubscribe/open/is_finished 保持不变。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.services.eventbus.redis_stream import RedisEventStream

logger = logging.getLogger(__name__)

# 只有真正结束的运行才关闭通道；等待人工的运行还要继续推续跑事件
TERMINAL_EVENTS = ("run_finished",)


class WorkflowEventBus:
    """按 run_uid 索引的运行事件总线（Redis Stream 承载）。"""

    def __init__(self) -> None:
        self._stream = RedisEventStream("workflow", TERMINAL_EVENTS)

    def open(self, run_uid: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """为一次运行返回事件发布函数，交给运行服务的 on_event。"""

        async def publish(event: dict[str, Any]) -> None:
            await self._stream.publish(run_uid, event)

        return publish

    async def subscribe(
        self, run_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """订阅：返回（已发生的事件，待接收的队列）。

        先返回缓冲再挂队列，保证「补发」与「后续」之间不漏事件。
        """

        return await self._stream.subscribe(run_uid)

    def unsubscribe(
        self, run_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        self._stream.unsubscribe(run_uid, queue)

    def is_finished(self, run_uid: str) -> bool:
        return self._stream.is_finished(run_uid)

    async def drop(self, run_uid: str) -> None:
        await self._stream.drop(run_uid)


# 全局单例：Redis Stream 支持多 worker 跨进程广播
workflow_events = WorkflowEventBus()
