"""进程内任务的停止信号。

对话回合与工作流运行共用同一套「进行中登记 + 停止标记」语义，
避免两处各写一份状态机导致行为漂移。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class StopSignal:
    """一组任务的进行中登记与停止标记。"""

    def __init__(self, label: str) -> None:
        self._label = label
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._stopped: set[str] = set()

    def begin(self, task_id: str) -> None:
        """任务开始：登记为进行中，并清除上一次遗留的停止标记。"""

        with self._lock:
            self._active.add(task_id)
            self._stopped.discard(task_id)

    def request_stop(self, task_id: str) -> bool:
        """请求停止任务。

        返回 True 表示确有进行中的任务并已置停止标记；返回 False 表示它已经结束，
        调用方应如实告知用户「没有可停止的任务」。
        """

        with self._lock:
            if task_id not in self._active:
                logger.info("停止请求被忽略：%s已结束 task_id=%s", self._label, task_id)
                return False
            self._stopped.add(task_id)
        logger.info("收到停止请求 %s task_id=%s", self._label, task_id)
        return True

    def is_stopped(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._stopped

    def has_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active

    def end(self, task_id: str) -> None:
        """任务结束：清除进行中登记与停止标记。"""

        with self._lock:
            self._active.discard(task_id)
            self._stopped.discard(task_id)
