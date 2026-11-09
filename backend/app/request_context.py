"""请求上下文：request_id / user_id / session_id，供日志与审计读取。"""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
session_id_var: ContextVar[str] = ContextVar("session_id", default="-")


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def set_user_id(value: str) -> None:
    user_id_var.set(value)


def set_session_id(value: str) -> None:
    session_id_var.set(value)


def get_request_id() -> str:
    return request_id_var.get()
