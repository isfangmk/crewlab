"""从助理回答创建任务图草稿：HTTP 层验证建图 + 审计 + 会话属主校验。"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.chat import ChatMessage, ChatSession
from app.models.task_graph import TaskGraphNode, TaskGraphRun
from tests.conftest import login_access_token

TAG = "from-msg-graph"


@pytest.mark.asyncio
async def test_create_graph_from_message(env: dict[str, str], api_base: str) -> None:
    """专属会话+回答：建单节点草稿，审计记录可查；非属主 403。"""

    sid = f"s-{TAG}-{uuid.uuid4().hex[:8]}"
    mid = f"m-{TAG}-{uuid.uuid4().hex[:8]}"
    try:
        async with SessionLocal() as db:
            db.add(
                ChatSession(
                    id=sid,
                    title="转任务测试",
                    provider="p",
                    model="m",
                    adk_user_id="test-user",
                    owner_user_id=1,
                )
            )
            await db.flush()
            db.add(
                ChatMessage(
                    id=mid,
                    session_id=sid,
                    role="assistant",
                    kind="message",
                    content="这是要转成任务图的回答正文，用于验证一键复用链路。",
                    seq=1,
                )
            )
            await db.commit()

        async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
            if (await client.get("/healthz")).status_code != 200:
                pytest.skip("后端未就绪")
            token = await login_access_token(client, env)
            headers = {"Authorization": f"Bearer {token}"}

            res = await client.post(
                "/api/v1/task-graphs/from-message",
                headers=headers,
                json={"message_id": mid, "title": "测试任务"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            graph_uid = body["graph_uid"]
            assert body["session_id"] == sid
            assert body["title"] == "测试任务"

            async with SessionLocal() as db:
                graph = await db.scalar(
                    select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid)
                )
                assert graph is not None
                assert graph.status == "draft"
                assert graph.card_message_id == mid
                nodes = (
                    await db.scalars(
                        select(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                    )
                ).all()
                assert len(nodes) == 1
                assert "回答正文" in nodes[0].target

            # 审计记录：建图动作落库
            async with SessionLocal() as db:
                from app.models.audit import AuditLog

                audit = await db.scalar(
                    select(AuditLog).where(AuditLog.resource_id == graph_uid)
                )
                assert audit is not None
                assert audit.action == "task_graph.create_from_message"

            # 不存在的消息 → 404
            miss = await client.post(
                "/api/v1/task-graphs/from-message",
                headers=headers,
                json={"message_id": "no-such-message"},
            )
            assert miss.status_code == 404
    finally:
        async with SessionLocal() as db:
            graphs = (
                await db.scalars(
                    select(TaskGraphRun).where(TaskGraphRun.session_id == sid)
                )
            ).all()
            for graph in graphs:
                for node in (
                    await db.scalars(
                        select(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                    )
                ).all():
                    await db.delete(node)
                await db.delete(graph)
            msg = await db.get(ChatMessage, mid)
            if msg is not None:
                await db.delete(msg)
            session = await db.get(ChatSession, sid)
            if session is not None:
                await db.delete(session)
            await db.commit()
