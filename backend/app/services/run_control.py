"""工作流运行的停止信号。

与对话回合共用同一套状态机，实现见 task_control。
"""

from __future__ import annotations

from app.services.task_control import StopSignal

_signal = StopSignal("运行")


def begin_run(run_uid: str) -> None:
    """运行开始：登记为进行中。"""

    _signal.begin(run_uid)


def request_stop(run_uid: str) -> bool:
    """请求停止运行；已经结束的运行如实返回 False。"""

    return _signal.request_stop(run_uid)


def is_stopped(run_uid: str) -> bool:
    return _signal.is_stopped(run_uid)


def end_run(run_uid: str) -> None:
    """运行结束：清除进行中登记与停止标记。"""

    _signal.end(run_uid)
