"""工作流运行的停止信号（进程内）。

只有「正在跑的运行」才能被停止：已结束的运行要如实回 stopped=false，
不能让标记残留，否则下一次同 uid 的运行会一启动就收工。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active: set[str] = set()
_stopped: set[str] = set()


def begin_run(run_uid: str) -> None:
    """运行开始：登记为进行中，并清除上一次遗留的停止标记。"""

    with _lock:
        _active.add(run_uid)
        _stopped.discard(run_uid)


def request_stop(run_uid: str) -> bool:
    """请求停止这次运行。

    返回 True 表示确有进行中的运行并已置停止标记；返回 False 表示它已经结束，
    调用方应如实告知用户「没有可停止的运行」。
    """

    with _lock:
        if run_uid not in _active:
            logger.info("停止请求被忽略：运行已结束 run_uid=%s", run_uid)
            return False
        _stopped.add(run_uid)
    logger.info("收到停止运行请求 run_uid=%s", run_uid)
    return True


def is_stopped(run_uid: str) -> bool:
    with _lock:
        return run_uid in _stopped


def end_run(run_uid: str) -> None:
    """运行结束：清除进行中登记与停止标记。"""

    with _lock:
        _active.discard(run_uid)
        _stopped.discard(run_uid)
