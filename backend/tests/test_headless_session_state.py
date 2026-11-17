"""无人值守会话 state 注入：真实 ADK 会话服务里 key 原样可读。

code_interpreter 靠 tool_context.state[HEADLESS_SESSION_ID_KEY] 拿真实会话 id
（headless 的 tool_context.session 是临时会话）。这里验证注入机制本身：
create_session(state=...) 后 key 不丢、不被改名——这条链路断则产出物挂错会话。
"""

from __future__ import annotations

import pytest

from app.adk_runtime.headless import HEADLESS_SESSION_ID_KEY
from google.adk.sessions.in_memory_session_service import InMemorySessionService


@pytest.mark.asyncio
async def test_create_session_state_key_survives() -> None:
    real_session_id = "94fc6684-1c37-4ac2-acdc-d4e73c5774ec"
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="plan_step",
        user_id="plan_step",
        state={HEADLESS_SESSION_ID_KEY: real_session_id},
    )
    assert session.state.get(HEADLESS_SESSION_ID_KEY) == real_session_id
