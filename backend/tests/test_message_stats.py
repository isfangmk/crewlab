"""回答统计（耗时 / token）与建议问题指令闸门。

stats_json 只在运行结束（done）时挂到最后一条 assistant 气泡；
无统计保持 NULL——禁止假 0。建议问题块只在与用户直接对话的根 Agent 上启用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adk_runtime.agent_factory import FOLLOWUPS_INSTRUCTION, build_chat_agent
from app.adk_runtime.stream_bridge import SseFrame
from app.db import create_db_engine
from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.observation import ObservationTurn
from app.services.chat.chat_service import AdkFramePersister, message_to_wire


async def _persist_frames(frames: list[SseFrame], db_path) -> list[ChatMessage]:
    """临时 SQLite 文件库上回放帧序列，返回落库消息行。"""

    engine = create_db_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            db.add(
                ChatSession(
                    id="session-1",
                    title="统计测试",
                    provider="deepseek",
                    model="deepseek-flash",
                    adk_user_id="user-1",
                )
            )
            # 工具帧要挂观测记录：真实链路 start_chat_turn 先建 turn，这里同款预置
            db.add(
                ObservationTurn(
                    id="trace-1",
                    session_id="session-1",
                    agent_id="agent-1",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="running",
                )
            )
            await db.commit()
            persister = AdkFramePersister(db, "session-1", trace_id="trace-1")
            for frame in frames:
                await persister.persist(frame)
            rows = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == "session-1")
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)
    finally:
        await engine.dispose()


def _delta(message_id: str, text: str) -> SseFrame:
    return SseFrame(
        event="assistant_delta",
        data={"message_id": message_id, "delta": text, "partial": False},
    )


@pytest.mark.asyncio
async def test_done_stats_land_on_last_assistant_bubble(tmp_path) -> None:
    """done 帧的耗时 / token 只挂到本运行最后一条气泡；前面的气泡无统计。"""

    done = SseFrame(
        event="done",
        data={
            "usage": {"prompt_tokens": 900, "completion_tokens": 334, "total_tokens": 1234},
            "duration_ms": 3210,
        },
    )
    rows = await _persist_frames(
        [_delta("m1", "第一段"), _delta("m2", "第二段"), done], tmp_path / "stats1.db"
    )
    assert [r.id for r in rows] == ["m1", "m2"]
    assert rows[0].stats_json is None
    assert json.loads(rows[1].stats_json or "{}") == {
        "duration_ms": 3210,
        "prompt_tokens": 900,
        "completion_tokens": 334,
        "total_tokens": 1234,
    }


@pytest.mark.asyncio
async def test_done_without_stats_keeps_null(tmp_path) -> None:
    """停止路径的 done 没有 usage / duration：保持 NULL，不造假 0。"""

    rows = await _persist_frames(
        [_delta("m1", "你好"), SseFrame(event="done", data={})], tmp_path / "stats2.db"
    )
    assert rows[0].stats_json is None


@pytest.mark.asyncio
async def test_partial_usage_only_keeps_known_keys(tmp_path) -> None:
    """只有部分键时只落已知键（例如拿不到 prompt_tokens 时不补 0）。"""

    done = SseFrame(
        event="done",
        data={"usage": {"total_tokens": 500}, "duration_ms": 800},
    )
    rows = await _persist_frames([_delta("m1", "好"), done], tmp_path / "stats3.db")
    assert json.loads(rows[0].stats_json or "{}") == {
        "duration_ms": 800,
        "total_tokens": 500,
    }


def test_message_to_wire_exposes_stats() -> None:
    now = datetime.now(timezone.utc)
    with_stats = ChatMessage(
        id="m1",
        session_id="session-1",
        role="assistant",
        kind="message",
        content="你好",
        stats_json=json.dumps({"duration_ms": 10, "total_tokens": 5}),
        seq=1,
        created_at=now,
    )
    without_stats = ChatMessage(
        id="m2",
        session_id="session-1",
        role="assistant",
        kind="message",
        content="好",
        seq=2,
        created_at=now,
    )
    assert message_to_wire(with_stats)["stats"] == {"duration_ms": 10, "total_tokens": 5}
    assert message_to_wire(without_stats)["stats"] is None


def _think(message_id: str, text: str) -> SseFrame:
    return SseFrame(
        event="thinking_delta",
        data={"message_id": message_id, "delta": text, "partial": False},
    )


@pytest.mark.asyncio
async def test_thinking_segments_persist_in_arrival_order(tmp_path) -> None:
    """思考段落库 kind=thinking，与正文按到达顺序成行；统计只挂正文气泡。"""

    done = SseFrame(
        event="done",
        data={"usage": {"total_tokens": 10}, "duration_ms": 100},
    )
    rows = await _persist_frames(
        [_think("t1", "先想"), _think("t1", "一想"), _delta("m1", "答案"), done],
        tmp_path / "think1.db",
    )
    assert [(r.id, r.kind, r.content) for r in rows] == [
        ("t1", "thinking", "先想一想"),
        ("m1", "message", "答案"),
    ]
    assert rows[0].stats_json is None
    assert json.loads(rows[1].stats_json or "{}")["total_tokens"] == 10


@pytest.mark.asyncio
async def test_thinking_flushes_before_tool_card(tmp_path) -> None:
    """工具边界先落思考段：思考不能插到工具卡后面去。

    工具帧要写观测记录，观测服务走独立会话（真实库）；沿用
    test_session_stats 的口径：专属 id 进真库、用完即清。
    """

    import uuid as _uuid

    from sqlalchemy import delete as sa_delete

    from app.config import get_settings
    from app.models.observation import ObservationToolCall

    engine = create_db_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    trace_id = f"trace-think-{_uuid.uuid4().hex[:8]}"
    sid = f"s_think_{_uuid.uuid4().hex[:8]}"
    try:
        async with session_factory() as db:
            db.add(
                ChatSession(
                    id=sid,
                    title="思考边界测试",
                    provider="deepseek",
                    model="deepseek-flash",
                    adk_user_id="user-1",
                )
            )
            db.add(
                ObservationTurn(
                    id=trace_id,
                    session_id=sid,
                    agent_id="agent-1",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="running",
                )
            )
            await db.commit()
        async with session_factory() as db:
            persister = AdkFramePersister(db, sid, trace_id=trace_id)
            for frame in [
                _think("t1", "调工具前想的"),
                SseFrame(
                    event="tool_start",
                    data={
                        "tool_call_id": "c1",
                        "name": "query_metric",
                        "status": "pending",
                        "arguments": {"q": "cpu"},
                    },
                ),
                SseFrame(
                    event="tool_end",
                    data={
                        "tool_call_id": "c1",
                        "name": "query_metric",
                        "status": "success",
                        "duration_ms": 12,
                        "result": {"ok": True},
                        "error": "",
                    },
                ),
                _delta("m1", "结论"),
                SseFrame(event="done", data={}),
            ]:
                await persister.persist(frame)
            rows = list(
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == sid)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert [r.kind for r in rows] == ["thinking", "tool_card", "message"]
            assert rows[0].content == "调工具前想的"
            assert rows[2].content == "结论"
    finally:
        async with session_factory() as db:
            await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id == sid))
            await db.execute(
                sa_delete(ObservationToolCall).where(
                    ObservationToolCall.trace_id == trace_id
                )
            )
            turn = await db.get(ObservationTurn, trace_id)
            if turn is not None:
                await db.delete(turn)
            session = await db.get(ChatSession, sid)
            if session is not None:
                await db.delete(session)
            await db.commit()
        await engine.dispose()


def test_message_to_wire_passes_thinking_kind() -> None:
    now = datetime.now(timezone.utc)
    row = ChatMessage(
        id="t1",
        session_id="session-1",
        role="assistant",
        kind="thinking",
        content="想",
        seq=1,
        created_at=now,
    )
    wire = message_to_wire(row)
    assert wire["kind"] == "thinking"
    assert wire["role"] == "assistant"


def test_followups_instruction_gating() -> None:
    """建议问题块只装配进与用户直接对话的根 Agent。"""

    common = dict(
        provider_id="deepseek",
        model="deepseek-flash",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        agent_name="demo",
        agent_description="演示",
        persona_instruction="",
        thinking_enabled=False,
    )
    root = build_chat_agent(include_followups=True, **common)
    assert "```suggestions" in root.instruction
    assert FOLLOWUPS_INSTRUCTION in root.instruction
    # 建议问题必须以用户口吻出题、禁止模型口吻，防风格回退
    assert "用户自己的口吻" in FOLLOWUPS_INSTRUCTION
    assert "禁止用助理/模型的口吻" in FOLLOWUPS_INSTRUCTION

    no_followups = build_chat_agent(include_followups=False, **common)
    assert "```suggestions" not in no_followups.instruction
