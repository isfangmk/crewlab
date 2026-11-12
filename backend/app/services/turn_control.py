"""进行中对话回合的停止信号。

与工作流运行共用同一套状态机，实现见 task_control。
"""

from __future__ import annotations

from app.services.task_control import StopSignal

_signal = StopSignal("回合")


def begin_turn(session_id: str) -> None:
    """新回合开始：登记为进行中。"""

    _signal.begin(session_id)


def request_stop(session_id: str) -> bool:
    """请求停止回合；空闲会话如实返回 False。"""

    return _signal.request_stop(session_id)


def is_stopped(session_id: str) -> bool:
    return _signal.is_stopped(session_id)


def has_active_turn(session_id: str) -> bool:
    return _signal.has_active(session_id)


def end_turn(session_id: str) -> None:
    """回合结束：清除进行中登记与停止标记。"""

    _signal.end(session_id)
