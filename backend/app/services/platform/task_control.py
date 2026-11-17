"""任务的停止信号（跨 worker）。

对话回合、工作流运行、任务图节点共用同一套「进行中登记 + 停止标记」语义，
底层经 Redis PubSub 广播，保证 A worker 点停止、B worker 的任务也能收到。
is_stopped 保持同步（ADK 的 stop_check 要求同步 callable，且每帧高频调用）。
"""

from __future__ import annotations

from app.services.platform import redis_distributed


class StopSignal:
    """一组任务的进行中登记与停止标记（Redis 广播 + 本地同步读）。"""

    def __init__(self, label: str) -> None:
        self._prefix = f"{label}:"

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}{task_id}"

    async def begin(self, task_id: str) -> None:
        await redis_distributed.begin(self._key(task_id))

    async def request_stop(self, task_id: str) -> bool:
        return await redis_distributed.request_stop(self._key(task_id))

    def is_stopped(self, task_id: str) -> bool:
        return redis_distributed.is_stopped(self._key(task_id))

    def has_active(self, task_id: str) -> bool:
        return redis_distributed.has_active(self._key(task_id))

    async def end(self, task_id: str) -> None:
        await redis_distributed.end(self._key(task_id))
