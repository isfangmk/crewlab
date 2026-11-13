"""Artifact API 集成：落库 → 列表 → 下载 → 会话删除级联清理（真后端）。"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_orm.artifact import Artifact
from app.services import artifact_service
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
            user_id=1,
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

        # 下载：文件字节还原 base64；图表给 payload JSON
        file_item = next(i for i in items if i["kind"] == "file")
        dl = await client.get(f"/api/v1/artifacts/{file_item['id']}/download", headers=headers)
        assert dl.status_code == 200
        assert dl.content == b"A,B\n1,2\n"

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
            user_id=1,
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
