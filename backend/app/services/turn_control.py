"""进行中对话回合的停止信号（进程内）。

只有「正在跑的回合」才能被停止：空闲会话的停止请求必须显式失败，
不能留下标记去污染下一个回合。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_stopped: set[str] = set()
_active: set[str] = set()


def begin_turn(session_id: str) -> None:
    """新回合开始：登记为进行中，并清除上一轮停止标记。"""

    with _lock:
        _active.add(session_id)
        _stopped.discard(session_id)


def request_stop(session_id: str) -> bool:
    """请求停止会话的进行中回合。

    返回 True 表示确有进行中的回合并已置停止标记；
    返回 False 表示该会话当前没有进行中的回合（空闲），
    调用方应如实告知用户「没有可停止的回合」。
    """

    with _lock:
        if session_id not in _active:
            logger.info("停止请求被忽略：会话无进行中回合 session_id=%s", session_id)
            return False
        _stopped.add(session_id)
    logger.info("收到停止会话请求 session_id=%s", session_id)
    return True


def is_stopped(session_id: str) -> bool:
    with _lock:
        return session_id in _stopped


def has_active_turn(session_id: str) -> bool:
    with _lock:
        return session_id in _active


def end_turn(session_id: str) -> None:
    """回合结束：清除进行中登记与停止标记。"""

    with _lock:
        _active.discard(session_id)
        _stopped.discard(session_id)
