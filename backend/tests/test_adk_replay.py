"""ADK 事件流重建：编辑重发 / 分叉后模型上下文必须与投影一致。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.adk_runtime.session_store import get_adk_session_service
from app.adk_runtime.stream_bridge import APP_NAME
from app.services.chat_service import _replay_adk_events


async def _load_texts(session_id: str, user_id: str) -> list[tuple[str, str]]:
    svc = get_adk_session_service()
    loaded = await svc.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    assert loaded is not None
    out: list[tuple[str, str]] = []
    for event in loaded.events:
        assert event.content is not None
        assert event.content.parts is not None
        text = event.content.parts[0].text
        assert isinstance(text, str)
        out.append((event.author or '', text))
    return out


@pytest.mark.asyncio
async def test_replay_rebuilds_and_overwrites_adk_history() -> None:
    user_id = f"replay-user-{uuid.uuid4().hex[:8]}"
    session_id = f"replay-session-{uuid.uuid4().hex[:8]}"
    session = SimpleNamespace(id=session_id, adk_user_id=user_id)
    svc = get_adk_session_service()
    try:
        await _replay_adk_events(
            session=session,
            history=[
                ("user", "message", "问题一"),
                ("assistant", "message", "回答一"),
                ("assistant", "tool_card", None),
                ("user", "message", "问题二"),
            ],
        )
        assert await _load_texts(session_id, user_id) == [
            ('user', '问题一'),
            ('workbench_chat', '回答一'),
            ('user', '问题二'),
        ]

        # 编辑重发：截断后再回放，旧事件必须全部消失。
        await _replay_adk_events(
            session=session,
            history=[("user", "message", "改写后的问题")],
        )
        assert await _load_texts(session_id, user_id) == [('user', '改写后的问题')]
    finally:
        await svc.delete_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
