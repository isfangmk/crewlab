"""会话模型写回判定：显式选模写回，None 沿用不写回。

把「本回合请求模型要不要同步到会话」的判定抽成纯函数单测：
会话模型是主回合与下游（计划步骤/排查 worker）的唯一源，判定错会
导致「建计划用请求模型、执行用旧会话模型」的不一致。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.models.chat import ChatSession
from app.services.chat.chat_service import session_model_sync_target


def _session(provider: str, model: str) -> ChatSession:
    # 判定只读 provider/model 两字段，用轻量替身避免拉起 ORM
    return cast(Any, SimpleNamespace(provider=provider, model=model))


def test_explicit_model_returns_sync_target() -> None:
    """用户显式选了不同模型：写回目标就是请求值。"""
    session = _session("custom", "workbuddy/mimo-v2.6-pro")
    assert session_model_sync_target(session, "deepseek", "deepseek-flash") == (
        "deepseek",
        "deepseek-flash",
    )


def test_same_model_returns_none() -> None:
    """请求模型与会话一致：无需写回，避免无谓 commit。"""
    session = _session("deepseek", "deepseek-flash")
    assert session_model_sync_target(session, "deepseek", "deepseek-flash") is None


def test_inherited_call_returns_none() -> None:
    """编辑重发/trigger/publish 传 None：沿用会话模型，不写回。"""
    session = _session("custom", "workbuddy/mimo-v2.6-pro")
    assert session_model_sync_target(session, None, None) is None
    assert session_model_sync_target(session, "deepseek", None) is None
    assert session_model_sync_target(session, None, "deepseek-flash") is None
