"""分享链接管理：列表筛选/分页/可见性、撤销即失效、会话 JSON 导出。

不依赖可用模型：会话、评测运行与分享令牌都直接插库（沿用
test_chat_session_mgmt 的插桩口径）；撤销链路用 API 创建的真实 token
验证「撤销后 resolve 立即 404」。用例自带专属 tag，finally 全清理。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import create_db_engine
from app.models.chat import ChatSession
from app.models.eval import EvalDataset, EvalRun
from app.models.eval_share import EvalShareToken
from app.models.share import ShareToken
from app.models.user import User
from tests.conftest import login_access_token


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _cleanup(
    session_ids: list[str],
    dataset_ids: list[int],
    run_uids: list[str],
    user_ids: list[int],
) -> None:
    """插桩数据全清理：分享令牌、会话、评测链、临时用户。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if run_uids:
                await db.execute(
                    sa_delete(EvalShareToken).where(EvalShareToken.run_uid.in_(run_uids))
                )
                await db.execute(sa_delete(EvalRun).where(EvalRun.run_uid.in_(run_uids)))
            if dataset_ids:
                await db.execute(sa_delete(EvalDataset).where(EvalDataset.id.in_(dataset_ids)))
            if session_ids:
                await db.execute(sa_delete(ShareToken).where(ShareToken.session_id.in_(session_ids)))
                await db.execute(sa_delete(ChatSession).where(ChatSession.id.in_(session_ids)))
            if user_ids:
                await db.execute(sa_delete(User).where(User.id.in_(user_ids)))
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_share_list_filters_visibility_and_revoke(env: dict[str, str], api_base: str) -> None:
    """列表按 kind/状态/标题筛选与分页；非 admin 只见自己；撤销即失效且有权限边界。"""

    tag = f"sa-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    session_ids = [f"{tag}-one", f"{tag}-two"]
    dataset_ids: list[int] = []
    run_uids: list[str] = []
    user_ids: list[int] = []

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        admin_token = await login_access_token(client, env)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        try:
            # 两个临时用户：dev（开发）与 viewer（只读）
            dev_email, viewer_email = f"dev-{tag}@local", f"viewer-{tag}@local"
            for email, role in ((dev_email, "developer"), (viewer_email, "viewer")):
                resp = await client.post(
                    "/api/v1/users",
                    headers=admin_headers,
                    json={"email": email, "display_name": tag, "password": f"Pw-{tag}-x", "role": role},
                )
                assert resp.status_code == 200, resp.text
                user_ids.append(resp.json()["id"])
            dev_id, viewer_id = user_ids
            admin_id = (await client.get("/api/v1/me", headers=admin_headers)).json()["id"]
            dev_headers = {"Authorization": f"Bearer {await _login(client, dev_email, f'Pw-{tag}-x')}"}
            viewer_headers = {"Authorization": f"Bearer {await _login(client, viewer_email, f'Pw-{tag}-x')}"}

            # 插桩：2 个会话 + 1 个评测运行；分享令牌覆盖 自己/他人/过期/历史空创建人
            engine = create_db_engine(get_settings().database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    db.add_all(
                        [
                            ChatSession(
                                id=f"{tag}-one",
                                title=f"share-admin-{tag}-one",
                                provider="deepseek",
                                model="deepseek-flash",
                                adk_user_id="user-1",
                            ),
                            ChatSession(
                                id=f"{tag}-two",
                                title=f"share-admin-{tag}-two",
                                provider="deepseek",
                                model="deepseek-flash",
                                adk_user_id="user-1",
                            ),
                        ]
                    )
                    db.add(EvalDataset(name=f"ds-{tag}", description="分享管理"))
                    await db.flush()
                    dataset_id = (
                        await db.scalar(select(EvalDataset).where(EvalDataset.name == f"ds-{tag}"))
                    ).id
                    dataset_ids.append(dataset_id)
                    run_uid = f"{tag}-run"[:32]
                    run_uids.append(run_uid)
                    db.add(
                        EvalRun(
                            run_uid=run_uid,
                            dataset_id=dataset_id,
                            dataset_name=f"ds-{tag}",
                            agent_id="default",
                            provider="deepseek",
                            model="deepseek-flash",
                            status="completed",
                        )
                    )
                    db.add_all(
                        [
                            # dev 自己：一条有效、一条过期
                            ShareToken(
                                session_id=f"{tag}-one",
                                token_hash=f"hash-{tag}-dev-active",
                                created_by=dev_id,
                                expires_at=now + timedelta(days=1),
                            ),
                            ShareToken(
                                session_id=f"{tag}-one",
                                token_hash=f"hash-{tag}-dev-expired",
                                created_by=dev_id,
                                expires_at=now - timedelta(days=1),
                            ),
                            # admin 的分享：dev 不可见
                            ShareToken(
                                session_id=f"{tag}-two",
                                token_hash=f"hash-{tag}-admin-active",
                                created_by=admin_id,
                                expires_at=now + timedelta(days=1),
                            ),
                            # 评测分享：dev 自己一条 + 历史空创建人一条
                            EvalShareToken(
                                run_uid=run_uid,
                                token_hash=f"hash-{tag}-eval-dev",
                                created_by=dev_id,
                                expires_at=now + timedelta(days=1),
                            ),
                            EvalShareToken(
                                run_uid=run_uid,
                                token_hash=f"hash-{tag}-eval-null",
                                created_by=None,
                                expires_at=now + timedelta(days=1),
                            ),
                        ]
                    )
                    await db.commit()
            finally:
                await engine.dispose()

            # 计数按本轮 tag 收口：库里可能有历史冒烟留下的真实分享行，不能全库数数
            resp = await client.get(f"/api/v1/shares?q={tag}", headers=admin_headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["total"] == 5
            assert len(body["items"]) == 5
            page = await client.get(f"/api/v1/shares?q={tag}&limit=2&offset=0", headers=admin_headers)
            assert page.json()["total"] == 5
            assert len(page.json()["items"]) == 2
            assert all("creator" in item and "expired" in item for item in body["items"])

            # dev 只见自己创建的 3 条；viewer 一条没有（非 admin 排除空创建人行）
            dev_view = (await client.get(f"/api/v1/shares?q={tag}", headers=dev_headers)).json()
            assert dev_view["total"] == 3
            assert all(item["created_by"] == dev_id for item in dev_view["items"])
            viewer_view = (await client.get(f"/api/v1/shares?q={tag}", headers=viewer_headers)).json()
            assert viewer_view["total"] == 0

            # kind / 状态 / 标题筛选
            only_eval = (await client.get(f"/api/v1/shares?q={tag}&kind=eval", headers=admin_headers)).json()
            assert only_eval["total"] == 2
            assert all(item["kind"] == "eval" for item in only_eval["items"])
            only_active = (await client.get(f"/api/v1/shares?q={tag}&status=active", headers=admin_headers)).json()
            assert only_active["total"] == 4
            only_expired = (await client.get(f"/api/v1/shares?q={tag}&status=expired", headers=admin_headers)).json()
            assert only_expired["total"] == 1
            assert only_expired["items"][0]["expired"] is True
            by_title = (
                await client.get(f"/api/v1/shares?q={tag}-one", headers=admin_headers)
            ).json()
            assert by_title["total"] == 2
            assert all("one" in item["title"] for item in by_title["items"])

            # 撤销链路：API 建一条真实分享 → 撤销 → 免登录读链接 404
            created = await client.post(
                f"/api/v1/chat/sessions/{tag}-one/share",
                headers=dev_headers,
                json={"expires_in_hours": 24},
            )
            assert created.status_code == 200, created.text
            raw_token = created.json()["token"]
            public = await client.get(f"/api/v1/chat/shares/{raw_token}")
            assert public.status_code == 200
            from app.services.platform.auth_service import hash_token

            engine = create_db_engine(get_settings().database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    row = await db.scalar(
                        select(ShareToken).where(ShareToken.token_hash == hash_token(raw_token))
                    )
                    share_id = row.id
            finally:
                await engine.dispose()

            # 权限边界：viewer 不可撤（403）；dev 不可撤他人（403）
            forbidden = await client.delete(
                f"/api/v1/shares/session/{share_id}", headers=viewer_headers
            )
            assert forbidden.status_code == 403
            other = await client.get(f"/api/v1/shares?q={tag}-two", headers=admin_headers)
            other_id = next(i["id"] for i in other.json()["items"] if i["kind"] == "session")
            denied = await client.delete(f"/api/v1/shares/session/{other_id}", headers=dev_headers)
            assert denied.status_code == 403

            # 创建者撤销成功 → 链接即死
            revoked = await client.delete(f"/api/v1/shares/session/{share_id}", headers=dev_headers)
            assert revoked.status_code == 200, revoked.text
            assert (await client.get(f"/api/v1/chat/shares/{raw_token}")).status_code == 404

            # 评测分享撤销（admin 对空创建人历史行可撤）
            eval_rows = (await client.get(f"/api/v1/shares?q={tag}&kind=eval", headers=admin_headers)).json()
            null_row = next(i for i in eval_rows["items"] if i["created_by"] is None)
            admin_revoke = await client.delete(
                f"/api/v1/shares/eval/{null_row['id']}", headers=admin_headers
            )
            assert admin_revoke.status_code == 200
            bad_kind = await client.delete("/api/v1/shares/bogus/1", headers=admin_headers)
            assert bad_kind.status_code == 400
        finally:
            await _cleanup(list(session_ids), dataset_ids, run_uids, user_ids)


@pytest.mark.asyncio
async def test_session_export_json_and_markdown(env: dict[str, str], api_base: str) -> None:
    """会话导出 format=json 返回结构化消息；默认 markdown 不回归。"""

    tag = f"se-{uuid.uuid4().hex[:8]}"
    session_ids = [f"{tag}-s"]
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            engine = create_db_engine(get_settings().database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    db.add(
                        ChatSession(
                            id=f"{tag}-s",
                            title=f"share-admin-{tag}-export",
                            provider="deepseek",
                            model="deepseek-flash",
                            adk_user_id="user-1",
                        )
                    )
                    await db.commit()
            finally:
                await engine.dispose()

            md = await client.get(f"/api/v1/chat/sessions/{tag}-s/export", headers=headers)
            assert md.status_code == 200, md.text
            assert md.headers["content-type"].startswith("text/markdown")
            assert f"share-admin-{tag}-export" in md.text

            js = await client.get(
                f"/api/v1/chat/sessions/{tag}-s/export?format=json", headers=headers
            )
            assert js.status_code == 200, js.text
            assert js.headers["content-type"].startswith("application/json")
            payload = js.json()
            assert payload["session"]["id"] == f"{tag}-s"
            assert payload["items"] == []
            assert payload["total"] == 0

            bad = await client.get(
                f"/api/v1/chat/sessions/{tag}-s/export?format=bogus", headers=headers
            )
            assert bad.status_code == 422
        finally:
            await _cleanup(list(session_ids), [], [], [])
