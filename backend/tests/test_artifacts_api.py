"""Artifact API 集成：落库 → 列表 → 下载 → 分享下载 → 截断/分叉（真后端）。"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_orm.artifact import Artifact
from app.services import artifact_service, chat_service, share_service
from app.services.chat_service import create_session
from app.tools.code_interpreter import remember_pending
from tests.conftest import login_access_token


async def test_artifacts_list_and_download(env: dict[str, str], api_base: str) -> None:
    """进程内落库真实会话的 artifact，走 HTTP 验证列表与下载。"""

    async with SessionLocal() as db:
        session = await create_session(
            db,
            title="artifact-api-test",
            provider="deepseek",
            model="deepseek-flash",
            agent_id="default",
            user_id="1",
        )
        session_id = session.id
        await db.commit()

    # 模拟落库器路径：pending 暂存 → save_from_tool_result
    remember_pending(
        "artifact-api-fc",
        [
            {
                "kind": "chart",
                "title": "趋势图",
                "mime": "application/json",
                "payload": {"series": [{"type": "bar", "data": [1, 2, 3]}]},
            },
            {
                "kind": "file",
                "title": "result.csv",
                "mime": "text/csv",
                "payload": {"name": "result.csv", "data": "QSxCCjEsMgo="},
            },
        ],
    )
    async with SessionLocal() as db:
        rows = await artifact_service.save_from_tool_result(
            db,
            session_id=session_id,
            tool_call_id="artifact-api-fc",
            tool_name="run_python",
        )
        assert len(rows) == 2
        await db.commit()

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        # 列表：chart/file 两个产出物
        listing = await client.get(
            f"/api/v1/chat/sessions/{session_id}/artifacts", headers=headers
        )
        assert listing.status_code == 200, listing.text
        items = listing.json()["items"]
        assert {i["kind"] for i in items} == {"chart", "file"}
        # 产出物挂在工具卡消息上，截断/重发才能按消息清理
        assert {i["message_id"] for i in items} == {"tool-card-artifact-api-fc"}
        # 列表顺序按执行内序号稳定：图表先于文件
        assert [i["kind"] for i in items] == ["chart", "file"]

        # 下载：文件字节还原 base64；图表给 payload JSON
        file_item = next(i for i in items if i["kind"] == "file")
        dl = await client.get(f"/api/v1/artifacts/{file_item['id']}/download", headers=headers)
        assert dl.status_code == 200
        assert dl.content == b"A,B\n1,2\n"

        # 只读分享页无登录态：用分享令牌下载，且必须校验 Artifact 属于该会话
        async with SessionLocal() as db:
            raw_token, _row = await share_service.create_share(
                db,
                session_id=session_id,
                created_by=1,
                expires_in_hours=1,
            )
        shared_dl = await client.get(
            f"/api/v1/chat/shares/{raw_token}/artifacts/{file_item['id']}/download"
        )
        assert shared_dl.status_code == 200, shared_dl.text
        assert shared_dl.content == b"A,B\n1,2\n"
        bad_token_dl = await client.get(
            f"/api/v1/chat/shares/not-a-token/artifacts/{file_item['id']}/download"
        )
        assert bad_token_dl.status_code == 404

        chart_item = next(i for i in items if i["kind"] == "chart")
        chart_dl = await client.get(
            f"/api/v1/artifacts/{chart_item['id']}/download", headers=headers
        )
        assert chart_dl.status_code == 200
        assert json.loads(chart_dl.content)["series"][0]["data"] == [1, 2, 3]

        # 清理：删会话 → artifacts 级联清零
        dele = await client.delete(f"/api/v1/chat/sessions/{session_id}", headers=headers)
        assert dele.status_code == 200, dele.text


async def test_session_delete_cascades_artifacts(env: dict[str, str], api_base: str) -> None:
    """会话删除后 artifacts 级联清零（FK ondelete=CASCADE）。"""

    async with SessionLocal() as db:
        session = await create_session(
            db,
            title="artifact-cascade-test",
            provider="deepseek",
            model="deepseek-flash",
            agent_id="default",
            user_id="1",
        )
        session_id = session.id
        await db.commit()
        remember_pending(
            "artifact-cascade-fc",
            [{"kind": "chart", "title": "t", "mime": "application/json", "payload": {"a": 1}}],
        )
        await artifact_service.save_from_tool_result(
            db,
            session_id=session_id,
            tool_call_id="artifact-cascade-fc",
            tool_name="run_python",
        )
        await db.commit()
        count = await db.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.session_id == session_id)
        )
        assert count == 1
        await db.commit()

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        dele = await client.delete(
            f"/api/v1/chat/sessions/{session_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert dele.status_code == 200, dele.text

    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.session_id == session_id)
        )
        assert count == 0


async def _seed_tool_card_artifact(session_id: str, tool_call_id: str) -> tuple[str, str]:
    """造一条用户消息 + 工具卡，并把 run_python 产出物落库。"""

    user_message_id = f"user-{tool_call_id}"
    card_message_id = f"tool-card-{tool_call_id}"
    async with SessionLocal() as db:
        await chat_service._add_message(
            db,
            message_id=user_message_id,
            session_id=session_id,
            role="user",
            kind="message",
            content="画个图",
            tool_call=None,
        )
        await chat_service._add_message(
            db,
            message_id=card_message_id,
            session_id=session_id,
            role="tool",
            kind="tool_card",
            content=None,
            tool_call={"id": tool_call_id, "name": "run_python", "status": "success"},
        )
        remember_pending(
            tool_call_id,
            [
                {
                    "kind": "chart",
                    "title": "趋势图",
                    "mime": "application/json",
                    "payload": {"series": [{"type": "bar", "data": [1, 2]}]},
                }
            ],
        )
        await artifact_service.save_from_tool_result(
            db,
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name="run_python",
        )
        await db.commit()
    return user_message_id, card_message_id


async def test_truncate_deletes_artifacts(env: dict[str, str], api_base: str) -> None:
    """编辑重发截断后，被删工具卡的 Artifact 必须一起清掉。"""

    tool_call_id = f"artifact-trunc-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        session = await create_session(
            db,
            title="artifact-truncate-test",
            provider="deepseek",
            model="deepseek-flash",
            agent_id="default",
            user_id="1",
        )
        session_id = session.id
        await db.commit()
    user_message_id, _card_id = await _seed_tool_card_artifact(session_id, tool_call_id)

    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.session_id == session_id)
        )
        assert count == 1
        await chat_service.truncate_after_message(db, session_id, user_message_id)
        count = await db.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.session_id == session_id)
        )
        assert count == 0

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        await client.delete(
            f"/api/v1/chat/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )


async def test_fork_copies_artifacts(env: dict[str, str], api_base: str) -> None:
    """分叉前缀内的 Artifact 复制到新会话，并挂到新工具卡消息上。"""

    tool_call_id = f"artifact-fork-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        session = await create_session(
            db,
            title="artifact-fork-test",
            provider="deepseek",
            model="deepseek-flash",
            agent_id="default",
            user_id="1",
        )
        session_id = session.id
        await db.commit()
    _user_message_id, card_id = await _seed_tool_card_artifact(session_id, tool_call_id)

    forked_id = ""
    async with SessionLocal() as db:
        forked = await chat_service.fork_session(
            db,
            session_id,
            up_to_message_id=card_id,
            owner_user_id=1,
            adk_user_id=f"fork-user-{uuid.uuid4().hex[:8]}",
        )
        forked_id = forked.id
        rows = list(
            (
                await db.scalars(
                    select(Artifact).where(Artifact.session_id == forked.id)
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].session_id == forked.id
        assert rows[0].tool_call_id == tool_call_id
        new_messages = await chat_service.list_messages(db, forked.id)
        new_card = next(m for m in new_messages if m.kind == "tool_card")
        assert rows[0].message_id == new_card.id
        assert rows[0].message_id != card_id
        assert rows[0].payload_json == json.dumps(
            {"series": [{"type": "bar", "data": [1, 2]}]}, ensure_ascii=False
        )

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        for sid in (forked_id, session_id):
            await client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers)
