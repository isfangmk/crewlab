"""@ 提及扩展到 技能/工作流/助理/会话：检索过滤 + hydrate 注入 + 非法类型。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.agent import UserAgent
from app.models.chat import ChatSession
from app.models.skill import UserSkill
from app.models.workflow import Workflow
from app.services.chat.chat_service import _hydrate_mentions
from app.services.chat.mention_service import MENTION_TYPES, search_mentions

TAG = f"mention-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_mention_types_and_search_hydrate() -> None:
    """四类新提及可检索、可 hydrate；会话按属主过滤；非法类型显式失败。"""

    owner = 930001
    other = 930002
    skill_key = f"skill_{TAG}"
    wf_name = f"wf_{TAG}"
    agent_name = f"agent_{TAG}"
    sid = f"s-{TAG}"
    sid_other = f"s-other-{TAG}"

    try:
        async with SessionLocal() as db:
            db.add(
                UserSkill(
                    key=skill_key,
                    description=f"{TAG} 技能描述",
                    risk="safe",
                    skill_group="other",
                    instructions="x",
                    preapproved_tools="",
                )
            )
            wf = Workflow(
                name=wf_name,
                description=f"{TAG} 工作流描述",
                definition_json="{}",
                published_definition_json="",
            )
            db.add(wf)
            db.add(
                UserAgent(
                    name=agent_name,
                    display_name=f"{TAG}助理",
                    description=f"{TAG} 助理描述",
                    when_to_use="x",
                    system_prompt="x",
                    critical_reminder="",
                    allowed_tools_json="[]",
                    disallowed_tools_json="[]",
                    permission_mode="default",
                    model="m",
                    max_turns=5,
                )
            )
            db.add(
                ChatSession(
                    id=sid, title=f"{TAG} 我的会话", provider="p", model="m",
                    adk_user_id=str(owner), owner_user_id=owner,
                )
            )
            db.add(
                ChatSession(
                    id=sid_other, title=f"{TAG} 他人会话", provider="p", model="m",
                    adk_user_id=str(other), owner_user_id=other,
                )
            )
            await db.commit()
            await db.refresh(wf)
            wf_id = str(wf.id)

        assert MENTION_TYPES == {"file", "server", "skill", "workflow", "agent", "session"}

        async with SessionLocal() as db:
            skills = await search_mentions(db, q=TAG, mention_type="skill", limit=10)
            assert any(s["id"] == skill_key for s in skills)

            workflows = await search_mentions(db, q=TAG, mention_type="workflow", limit=10)
            assert any(w["id"] == wf_id for w in workflows)

            agents = await search_mentions(db, q=TAG, mention_type="agent", limit=10)
            assert any(a["id"] == agent_name for a in agents)

            sessions = await search_mentions(
                db, q=TAG, mention_type="session", limit=10, owner_user_id=owner
            )
            assert all(s["id"] == sid for s in sessions)  # 跨用户会话被过滤

            # 非法类型
            with pytest.raises(ValueError, match="不支持的 type"):
                await search_mentions(db, q="x", mention_type="bogus", limit=10)

            # hydrate：四类都能注入真实对象元数据
            ctx = await _hydrate_mentions(
                db,
                [
                    {"type": "skill", "id": skill_key},
                    {"type": "workflow", "id": wf_id},
                    {"type": "agent", "id": agent_name},
                    {"type": "session", "id": sid},
                ],
            )
            assert "[技能]" in ctx and skill_key in ctx
            assert "[工作流]" in ctx and wf_name in ctx
            assert "[助理]" in ctx and f"{TAG}助理" in ctx
            assert "[会话]" in ctx and sid in ctx

            # hydrate 查不到显式失败；非法类型显式失败
            with pytest.raises(LookupError, match="技能不存在"):
                await _hydrate_mentions(db, [{"type": "skill", "id": "no-such"}])
            with pytest.raises(ValueError, match="不支持的提及类型"):
                await _hydrate_mentions(db, [{"type": "bogus", "id": "x"}])
    finally:
        async with SessionLocal() as db:
            for s in (sid, sid_other):
                session = await db.get(ChatSession, s)
                if session is not None:
                    await db.delete(session)
            agent = await db.get(UserAgent, agent_name)
            if agent is not None:
                await db.delete(agent)
            skill = await db.get(UserSkill, skill_key)
            if skill is not None:
                await db.delete(skill)
            wf_row = await db.scalar(select(Workflow).where(Workflow.name == wf_name))
            if wf_row is not None:
                await db.delete(wf_row)
            await db.commit()
