"""并行排查 API：列表/快照、SSE 补发回放、停止与重试的权限边界。

真后端 + 直接插库（沿用 test_share_admin 口径）；用例自带专属 tag，
finally 全清理。重试链路走真实后端后台任务（装配失败记 failed，验证状态机）。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import create_db_engine
from app.models.chat import ChatSession
from app.models.investigation import InvestigationDispatch, InvestigationWorkerRun
from app.models.user import User
from tests.conftest import login_access_token


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _cleanup(
    session_ids: list[str], dispatch_uids: list[str], user_ids: list[int]
) -> None:
    """插桩数据全清理：worker 行、派发行、临时会话与临时用户。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if dispatch_uids:
                rows = (
                    (
                        await db.scalars(
                            select(InvestigationDispatch).where(
                                InvestigationDispatch.dispatch_uid.in_(dispatch_uids)
                            )
                        )
                    )
                    .all()
                )
                ids = [r.id for r in rows]
                if ids:
                    await db.execute(
                        sa_delete(InvestigationWorkerRun).where(
                            InvestigationWorkerRun.dispatch_id.in_(ids)
                        )
                    )
                    await db.execute(
                        sa_delete(InvestigationDispatch).where(
                            InvestigationDispatch.id.in_(ids)
                        )
                    )
            if session_ids:
                await db.execute(sa_delete(ChatSession).where(ChatSession.id.in_(session_ids)))
            if user_ids:
                await db.execute(sa_delete(User).where(User.id.in_(user_ids)))
            await db.commit()
    finally:
        await engine.dispose()


async def _insert_fixture(
    *, session_id: str, dispatch_uid: str, created_by: int | None, worker_status: str
) -> None:
    """直插会话 + 一次排查 + 一路 worker（含 1 号发现）。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(
                ChatSession(
                    id=session_id,
                    title=f"inv-{session_id}",
                    provider="deepseek",
                    model="deepseek-flash",
                    adk_user_id="user-1",
                )
            )
            dispatch = InvestigationDispatch(
                dispatch_uid=dispatch_uid,
                session_id=session_id,
                question="q",
                status="succeeded" if worker_status == "succeeded" else "failed",
                config_json=json.dumps(
                    {
                        "workers": [{"name": "日志员", "target": "日志", "instruction": ""}],
                        "max_steps_per_worker": 4,
                        "timeout_seconds_per_worker": 30,
                        "token_budget_per_worker": 5000,
                    },
                    ensure_ascii=False,
                ),
                findings_json=json.dumps(
                    [
                        {
                            "n": 1,
                            "worker_uid": f"{dispatch_uid}-w",
                            "retired": False,
                            "title": "发现1",
                            "detail": "d",
                            "evidence": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                budget_json="{}",
                created_by=created_by,
            )
            db.add(dispatch)
            await db.flush()
            db.add(
                InvestigationWorkerRun(
                    dispatch_id=dispatch.id,
                    worker_uid=f"{dispatch_uid}-w",
                    name="日志员",
                    target="日志",
                    status=worker_status,
                    error="" if worker_status == "succeeded" else "上一轮失败",
                    findings_json=json.dumps(
                        {"conclusion": "c", "findings": []}, ensure_ascii=False
                    ),
                    budget_json="{}",
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_investigation_snapshot_and_permissions(env: dict[str, str], api_base: str) -> None:
    """快照 wire（全局编号发现）与停止/重试的权限边界。"""

    tag = f"ia-{uuid.uuid4().hex[:8]}"
    session_ids = [f"{tag}-s"]
    dispatch_uids = [f"{tag}-d"]
    user_ids: list[int] = []

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        admin_token = await login_access_token(client, env)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        try:
            # 两个临时用户：owner（开发）与 viewer（只读）
            owner_email = f"owner-{tag}@local"
            viewer_email = f"viewer-{tag}@local"
            for email, role in ((owner_email, "developer"), (viewer_email, "viewer")):
                resp = await client.post(
                    "/api/v1/users",
                    headers=admin_headers,
                    json={
                        "email": email,
                        "display_name": tag,
                        "password": f"Pw-{tag}-x",
                        "role": role,
                    },
                )
                assert resp.status_code == 200, resp.text
                user_ids.append(resp.json()["id"])
            owner_id, _viewer_id = user_ids
            owner_headers = {
                "Authorization": f"Bearer {await _login(client, owner_email, f'Pw-{tag}-x')}"
            }
            viewer_headers = {
                "Authorization": f"Bearer {await _login(client, viewer_email, f'Pw-{tag}-x')}"
            }

            await _insert_fixture(
                session_id=session_ids[0],
                dispatch_uid=dispatch_uids[0],
                created_by=owner_id,
                worker_status="succeeded",
            )

            # 快照：全局编号发现与统计
            resp = await client.get(
                f"/api/v1/investigations/{dispatch_uids[0]}", headers=owner_headers
            )
            assert resp.status_code == 200, resp.text
            wire = resp.json()
            assert wire["findings"][0]["n"] == 1
            assert wire["stats"]["total"] == 1

            # 列表：按会话过滤
            resp = await client.get(
                "/api/v1/investigations",
                params={"session_id": session_ids[0]},
                headers=owner_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["total"] == 1

            # 权限：viewer 不能停；admin 可停（无可停止任务时 stopped=false）
            resp = await client.post(
                f"/api/v1/investigations/{dispatch_uids[0]}/stop", headers=viewer_headers
            )
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"/api/v1/investigations/{dispatch_uids[0]}/stop", headers=admin_headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["stopped"] is False

            # 重试：非失败 worker → 409
            resp = await client.post(
                f"/api/v1/investigations/{dispatch_uids[0]}/workers/{dispatch_uids[0]}-w/retry",
                headers=owner_headers,
            )
            assert resp.status_code == 409, resp.text

            # 不存在的排查 → 404
            resp = await client.get(
                "/api/v1/investigations/not-exist-uid", headers=owner_headers
            )
            assert resp.status_code == 404, resp.text
        finally:
            await _cleanup(session_ids, dispatch_uids, user_ids)


@pytest.mark.asyncio
async def test_retry_then_events_replay(env: dict[str, str], api_base: str) -> None:
    """重试真实后台任务 → SSE 补发回放 worker 生命周期事件。"""

    tag = f"ia-{uuid.uuid4().hex[:8]}"
    session_ids = [f"{tag}-s"]
    dispatch_uids = [f"{tag}-d"]

    async with httpx.AsyncClient(base_url=api_base, timeout=90.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        admin_token = await login_access_token(client, env)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        try:
            await _insert_fixture(
                session_id=session_ids[0],
                dispatch_uid=dispatch_uids[0],
                created_by=None,
                worker_status="failed",
            )

            # 重试：后端起真实后台任务（装配失败 → worker 记 failed → dispatch_finished）
            resp = await client.post(
                f"/api/v1/investigations/{dispatch_uids[0]}/workers/{dispatch_uids[0]}-w/retry",
                headers=admin_headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["attempt"] == 2

            # 轮询快照到终态
            status = ""
            for _ in range(60):
                resp = await client.get(
                    f"/api/v1/investigations/{dispatch_uids[0]}", headers=admin_headers
                )
                status = resp.json()["status"]
                if status in ("succeeded", "failed", "stopped"):
                    break
                await asyncio.sleep(0.5)
            assert status in ("succeeded", "failed", "stopped")

            # SSE：补发回放应含 worker 生命周期事件并正常收尾
            events: list[str] = []
            async with client.stream(
                "GET",
                f"/api/v1/investigations/{dispatch_uids[0]}/events",
                headers=admin_headers,
            ) as resp:
                assert resp.status_code == 200, resp.status_code
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        events.append(line.split(":", 1)[1].strip())
                    if "dispatch_finished" in events:
                        break
            assert "worker_started" in events
            assert "worker_finished" in events
            assert "dispatch_finished" in events
        finally:
            await _cleanup(session_ids, dispatch_uids, [])
