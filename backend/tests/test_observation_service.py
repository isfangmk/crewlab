"""观测埋点单元测试：用 mock SessionLocal 隔离落库。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import observation_service


@pytest.mark.asyncio
async def test_start_and_end_ok_persists_tokens() -> None:
    added: list[object] = []

    class FakeSession:
        def add(self, row: object) -> None:
            added.append(row)

        async def commit(self) -> None:
            return None

        async def get(self, _model: object, trace_id: str) -> object:
            row = added[0]
            assert getattr(row, "id") == trace_id
            return row

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch.object(observation_service, "SessionLocal", return_value=FakeSession()):
        with patch.object(observation_service, "get_request_id", return_value="rid-1"):
            obs = await observation_service.start_chat_turn(
                session_id="sess-1",
                agent_id="default",
                provider="deepseek",
                model="deepseek-flash",
                user_input="现在几点了",
            )

    assert len(added) == 1
    row = added[0]
    assert getattr(row, "session_id") == "sess-1"
    assert getattr(row, "status") == "running"
    assert getattr(row, "input_summary") == "现在几点了"
    assert obs.trace_id == getattr(row, "id")

    with patch.object(observation_service, "SessionLocal", return_value=FakeSession()):
        await obs.end_ok(
            usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
        )

    assert getattr(row, "status") == "ok"
    assert getattr(row, "prompt_tokens") == 10
    assert getattr(row, "completion_tokens") == 4
    assert getattr(row, "total_tokens") == 14
    assert isinstance(getattr(row, "duration_ms"), int)


@pytest.mark.asyncio
async def test_end_error_sets_message() -> None:
    row = SimpleNamespace(
        id="t1",
        status="running",
        duration_ms=None,
        ended_at=None,
        error_message=None,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
    )

    class FakeSession:
        def add(self, _row: object) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def get(self, _model: object, _trace_id: str) -> object:
            return row

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    obs = observation_service.ChatTurnObservation(trace_id="t1", _started_at=0.0)
    with patch.object(observation_service, "SessionLocal", return_value=FakeSession()):
        await obs.end_error(RuntimeError("boom"))
    assert row.status == "error"
    assert row.error_message == "RuntimeError: boom"


def test_split_usage_skips_null() -> None:
    assert observation_service._split_usage(None) == (None, None, None)
    assert observation_service._split_usage(
        {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    ) == (None, None, None)
    assert observation_service._split_usage(
        {"prompt_tokens": 3, "completion_tokens": None, "total_tokens": None}
    ) == (3, None, None)


def test_turn_to_wire() -> None:
    from datetime import datetime, timezone

    row = SimpleNamespace(
        id="tid",
        session_id="s",
        agent_id="default",
        provider="deepseek",
        model="m",
        status="ok",
        request_id="r",
        input_summary="hi",
        error_message=None,
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        duration_ms=12,
        tool_call_count=2,
        skill_call_count=1,
        started_at=datetime(2026, 11, 9, tzinfo=timezone.utc),
        ended_at=None,
    )
    wire = observation_service.turn_to_wire(row)  # type: ignore[arg-type]
    assert wire["trace_id"] == "tid"
    assert wire["total_tokens"] == 3
    assert wire["tool_call_count"] == 2
    assert wire["skill_call_count"] == 1


def test_classify_tool_kinds() -> None:
    assert observation_service.classify_tool("load_skill") == ("skill", "")
    assert observation_service.classify_tool("list_skills") == ("skill", "")
    assert observation_service.classify_tool("mcp__github__search_repos") == ("mcp", "github")
    assert observation_service.classify_tool("web_search") == ("tool", "")
