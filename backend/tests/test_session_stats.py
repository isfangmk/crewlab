"""会话列表统计：产出物个数按 session_id 聚合；待审/待输入分开计数。"""

from __future__ import annotations

import uuid

import pytest

from app.db import create_db_engine
from app.models.approval import Approval
from app.models.artifact import Artifact
from app.models.chat import ChatSession
from app.services.chat.chat_service import list_session_stats


def _new_sessionmaker():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_db_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _session_row(session_id: str) -> ChatSession:
    return ChatSession(
        id=session_id,
        title=f"统计测试-{session_id}",
        provider="deepseek",
        model="deepseek-flash",
        adk_user_id=f"user-{session_id}",
    )


def _artifact_row(session_id: str, seq: int) -> Artifact:
    return Artifact(
        id=f"art_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
        message_id=f"tool-card-{uuid.uuid4().hex[:8]}",
        seq=seq,
        kind="chart",
        title=f"图表 {seq}",
    )


@pytest.mark.asyncio
async def test_list_session_stats_counts_artifacts() -> None:
    engine, sessionmaker = _new_sessionmaker()
    sid = f"s_{uuid.uuid4().hex[:8]}"
    other = f"s_{uuid.uuid4().hex[:8]}"
    try:
        async with sessionmaker() as db:
            # 先落会话再落产出物：产出物外键指向会话，两段提交保证顺序
            db.add(_session_row(sid))
            db.add(_session_row(other))
            await db.commit()
            rows = [_artifact_row(sid, i) for i in range(3)]
            for row in rows:
                db.add(row)
            await db.commit()

            stats = await list_session_stats(db, [sid, other])
            assert stats[sid].artifact_count == 3
            # 没有产出物的会话按 0 计，不缺字段
            assert stats[other].artifact_count == 0

            for row in rows:
                await db.delete(row)
            await db.delete(await db.get(ChatSession, sid))
            await db.delete(await db.get(ChatSession, other))
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_session_stats_artifacts_are_per_session() -> None:
    """不同会话的产出物互不串数。"""

    engine, sessionmaker = _new_sessionmaker()
    sid_a = f"s_{uuid.uuid4().hex[:8]}"
    sid_b = f"s_{uuid.uuid4().hex[:8]}"
    rows = [_artifact_row(sid_a, 0), _artifact_row(sid_b, 0), _artifact_row(sid_b, 1)]
    try:
        async with sessionmaker() as db:
            # 先落会话再落产出物：产出物外键指向会话，两段提交保证顺序
            db.add(_session_row(sid_a))
            db.add(_session_row(sid_b))
            await db.commit()
            for row in rows:
                db.add(row)
            await db.commit()

            stats = await list_session_stats(db, [sid_a, sid_b])
            assert stats[sid_a].artifact_count == 1
            assert stats[sid_b].artifact_count == 2

            for row in rows:
                await db.delete(row)
            await db.delete(await db.get(ChatSession, sid_a))
            await db.delete(await db.get(ChatSession, sid_b))
            await db.commit()
    finally:
        await engine.dispose()


def _approval_row(session_id: str, kind: str) -> "Approval":
    suffix = uuid.uuid4().hex[:12]
    return Approval(
        id=f"ap_{suffix}",
        session_id=session_id,
        confirmation_fc_id=f"fc_{suffix}",
        tool_call_id=f"call_{suffix}",
        tool_name="demo",
        kind=kind,
        title=f"待办 {kind}",
        summary="",
        payload="{}",
        hint="",
        status="pending",
    )


@pytest.mark.asyncio
async def test_pending_approvals_and_inputs_count_separately() -> None:
    """审批与澄清提问是两种待办：待审只算操作确认，待输入只算 user_input。"""

    engine, sessionmaker = _new_sessionmaker()
    sid = f"s_{uuid.uuid4().hex[:8]}"
    try:
        async with sessionmaker() as db:
            db.add(_session_row(sid))
            await db.commit()
            rows = [
                _approval_row(sid, "tool_confirmation"),
                _approval_row(sid, "tool_confirmation"),
                _approval_row(sid, "user_input"),
            ]
            for row in rows:
                db.add(row)
            await db.commit()

            stats = await list_session_stats(db, [sid])
            assert stats[sid].pending_approvals == 2
            assert stats[sid].pending_inputs == 1

            for row in rows:
                await db.delete(row)
            await db.delete(await db.get(ChatSession, sid))
            await db.commit()
    finally:
        await engine.dispose()
