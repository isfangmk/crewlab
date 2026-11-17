"""任务图事件总线：按 graph_uid 缓冲 + 多订阅者，支持断线重连补发。

一次图运行的节点进度要能被多个订阅者接续看到（右栏工作台重连、对话页各连各的流）。
订阅方连上来时先补发已发生事件，再挂队列收后续事件，画面不缺步骤。
终态 graph_finished 关闭通道、回收缓冲。

多 worker 部署：底层用 Redis Stream 跨进程广播（app.services.eventbus），
公开接口 publish/subscribe/unsubscribe/open/is_finished 保持不变。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.services.eventbus.redis_stream import RedisEventStream

logger = logging.getLogger(__name__)

# 只有终态事件才关闭通道；汇总 delta 等后续事件仍在同一条流里。
TERMINAL_EVENTS = ("graph_finished",)


class TaskGraphEventBus:
    """按 graph_uid 索引的运行事件总线（Redis Stream 承载）。"""

    def __init__(self) -> None:
        self._stream = RedisEventStream("task_graph", TERMINAL_EVENTS)

    def open(self, graph_uid: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """为一次运行返回事件发布函数。"""

        async def publish(event: dict[str, Any]) -> None:
            await self._stream.publish(graph_uid, event)

        return publish

    async def subscribe(
        self, graph_uid: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """先挂队列再取缓冲，保证补发与后续之间不漏事件。"""

        return await self._stream.subscribe(graph_uid)

    def unsubscribe(
        self, graph_uid: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        self._stream.unsubscribe(graph_uid, queue)

    def is_finished(self, graph_uid: str) -> bool:
        return self._stream.is_finished(graph_uid)

    async def drop(self, graph_uid: str) -> None:
        await self._stream.drop(graph_uid)


# 全局单例：Redis Stream 支持多 worker 跨进程广播
task_graph_events = TaskGraphEventBus()
