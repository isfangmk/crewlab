"""会话级免审：批准时勾「后续不再询问」后确认名单内工具放行；澄清提问不受影响。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from google.adk.tools.tool_confirmation import ToolConfirmation

from app.adk_runtime.tool_approval import (
    _PENDING_CONFIRMATION_ERROR,
    SESSION_TRUST_KEY,
    build_before_tool_callback,
)


def _fake_tool_context(confirmation: ToolConfirmation | None, state: dict | None = None):
    """手写 tool_context 双替身（同 test_ask_user 的 SimpleNamespace 风格）。"""

    requested: list[str] = []

    def request_confirmation(hint: str = "") -> None:
        requested.append(hint)

    ctx = SimpleNamespace(
        tool_confirmation=confirmation,
        actions=SimpleNamespace(skip_summarization=False),
        function_call_id=f"call_{uuid.uuid4().hex[:8]}",
        request_confirmation=request_confirmation,
        state={} if state is None else state,
        session=SimpleNamespace(id="session-1"),
    )
    return ctx, requested


class _FakeTool:
    name = "mcp__add-work__whoami"


@pytest.mark.asyncio
async def test_trust_payload_opens_session_trust() -> None:
    """确认帧带 trust_session 时置会话级免审并放行本次调用。"""

    callback = build_before_tool_callback({"mcp__add-work__whoami"})
    ctx, requested = _fake_tool_context(
        ToolConfirmation(confirmed=True, payload={"trust_session": True})
    )
    result = await callback(_FakeTool(), {"real_name": "测试"}, ctx)
    assert result is None
    assert requested == []
    assert ctx.state[SESSION_TRUST_KEY] is True


@pytest.mark.asyncio
async def test_session_trust_skips_confirmation() -> None:
    """已置免审的会话，名单内工具直接放行，不再弹审批。"""

    callback = build_before_tool_callback({"mcp__add-work__whoami"})
    ctx, requested = _fake_tool_context(None, state={SESSION_TRUST_KEY: True})
    result = await callback(_FakeTool(), {}, ctx)
    assert result is None
    assert requested == []


@pytest.mark.asyncio
async def test_without_trust_gate_still_pauses() -> None:
    """没有免审时闸门行为不变：暂停等确认。"""

    callback = build_before_tool_callback({"mcp__add-work__whoami"})
    ctx, requested = _fake_tool_context(None)
    result = await callback(_FakeTool(), {}, ctx)
    assert result == {"error": _PENDING_CONFIRMATION_ERROR}
    assert len(requested) == 1


@pytest.mark.asyncio
async def test_session_trust_does_not_skip_ask_user() -> None:
    """澄清提问不是审批：会话级免审不豁免 ask_user，照常暂停等作答。"""

    callback = build_before_tool_callback(set(), set())
    ctx, requested = _fake_tool_context(None, state={SESSION_TRUST_KEY: True})
    result = await callback(
        SimpleNamespace(name="ask_user"),
        {"question": "请补充中文姓名", "options": ["周一", "周三"], "fields": []},
        ctx,
    )
    assert result == {"error": _PENDING_CONFIRMATION_ERROR}
    assert len(requested) == 1
