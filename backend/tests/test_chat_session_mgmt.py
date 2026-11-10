"""真实会话：搜索/置顶/fork/导出/分页筛选。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.conftest import login_access_token


def _pick_model(catalog: dict) -> tuple[str, str]:
    providers = catalog.get("providers")
    if not isinstance(providers, list) or len(providers) == 0:
        raise LookupError("模型目录缺少 providers")
    p0 = providers[0]
    provider = p0.get("id")
    models = p0.get("models")
    if not isinstance(provider, str) or provider == "":
        raise LookupError("provider.id 无效")
    if not isinstance(models, list) or len(models) == 0:
        raise LookupError("provider.models 为空")
    model = models[0]
    if not isinstance(model, str) or model == "":
        raise LookupError("model 无效")
    return provider, model


@pytest.mark.asyncio
async def test_session_pin_fork_export(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        models = await client.get("/api/v1/aiops/models", headers=headers)
        if models.status_code != 200:
            pytest.skip("无可用模型")
        try:
            provider, model = _pick_model(models.json())
        except LookupError as exc:
            pytest.skip(str(exc))

        cr = await client.post(
            "/api/v1/chat/sessions",
            headers=headers,
            json={
                "title": "mgmt-test-session",
                "provider": provider,
                "model": model,
                "agent_id": "default",
            },
        )
        assert cr.status_code == 200, cr.text
        sid = cr.json()["id"]

        pinned = await client.patch(
            f"/api/v1/chat/sessions/{sid}/flags",
            headers=headers,
            json={"pinned": True},
        )
        assert pinned.status_code == 200, pinned.text
        assert pinned.json()["pinned"] is True

        listed = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": "mgmt-test"},
        )
        assert listed.status_code == 200, listed.text
        ids = [i["id"] for i in listed.json()["items"]]
        assert sid in ids

        exported = await client.get(
            f"/api/v1/chat/sessions/{sid}/export",
            headers=headers,
        )
        assert exported.status_code == 200, exported.text
        assert "mgmt-test-session" in exported.text


@pytest.mark.asyncio
async def test_session_list_filters_pagination_and_get(
    env: dict[str, str], api_base: str
) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        models = await client.get("/api/v1/aiops/models", headers=headers)
        if models.status_code != 200:
            pytest.skip("无可用模型")
        try:
            provider, model = _pick_model(models.json())
        except LookupError as exc:
            pytest.skip(str(exc))

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        created_ids: list[str] = []
        for i in range(3):
            cr = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": f"page-filter-{stamp}-{i}",
                    "provider": provider,
                    "model": model,
                    "agent_id": "default",
                },
            )
            assert cr.status_code == 200, cr.text
            created_ids.append(cr.json()["id"])

        got = await client.get(
            f"/api/v1/chat/sessions/{created_ids[0]}",
            headers=headers,
        )
        assert got.status_code == 200, got.text
        assert got.json()["id"] == created_ids[0]
        assert got.json()["title"] == f"page-filter-{stamp}-0"

        missing = await client.get(
            "/api/v1/chat/sessions/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert missing.status_code == 404, missing.text

        by_agent = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": f"page-filter-{stamp}", "agent_id": "default", "limit": 2, "offset": 0},
        )
        assert by_agent.status_code == 200, by_agent.text
        body = by_agent.json()
        assert body["total"] >= 3
        assert len(body["items"]) == 2
        assert all(i["agent_id"] == "default" for i in body["items"])

        page2 = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": f"page-filter-{stamp}", "limit": 2, "offset": 2},
        )
        assert page2.status_code == 200, page2.text
        assert page2.json()["total"] == body["total"]
        assert len(page2.json()["items"]) >= 1

        now = datetime.now(timezone.utc)
        in_window = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "q": f"page-filter-{stamp}",
                "updated_from": (now - timedelta(hours=1)).isoformat(),
                "updated_to": (now + timedelta(hours=1)).isoformat(),
                "limit": 20,
                "offset": 0,
            },
        )
        assert in_window.status_code == 200, in_window.text
        assert in_window.json()["total"] >= 3

        out_window = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "q": f"page-filter-{stamp}",
                "updated_from": (now - timedelta(days=30)).isoformat(),
                "updated_to": (now - timedelta(days=29)).isoformat(),
                "limit": 20,
                "offset": 0,
            },
        )
        assert out_window.status_code == 200, out_window.text
        assert out_window.json()["total"] == 0

        bad_range = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "updated_from": (now + timedelta(days=1)).isoformat(),
                "updated_to": now.isoformat(),
            },
        )
        assert bad_range.status_code == 422, bad_range.text


@pytest.mark.asyncio
async def test_session_sort_and_batch_delete(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        models = await client.get("/api/v1/aiops/models", headers=headers)
        if models.status_code != 200:
            pytest.skip("无可用模型")
        try:
            provider, model = _pick_model(models.json())
        except LookupError as exc:
            pytest.skip(str(exc))

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        titles = [f"sort-batch-{stamp}-c", f"sort-batch-{stamp}-a", f"sort-batch-{stamp}-b"]
        created_ids: list[str] = []
        for title in titles:
            cr = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": title,
                    "provider": provider,
                    "model": model,
                    "agent_id": "default",
                },
            )
            assert cr.status_code == 200, cr.text
            created_ids.append(cr.json()["id"])

        asc = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "q": f"sort-batch-{stamp}",
                "sort_by": "title",
                "sort_dir": "asc",
                "limit": 20,
                "offset": 0,
            },
        )
        assert asc.status_code == 200, asc.text
        asc_titles = [i["title"] for i in asc.json()["items"]]
        assert asc_titles == sorted(asc_titles)

        desc = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "q": f"sort-batch-{stamp}",
                "sort_by": "title",
                "sort_dir": "desc",
                "limit": 20,
                "offset": 0,
            },
        )
        assert desc.status_code == 200, desc.text
        desc_titles = [i["title"] for i in desc.json()["items"]]
        assert desc_titles == sorted(desc_titles, reverse=True)

        bad_sort = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"sort_by": "nope"},
        )
        assert bad_sort.status_code == 422, bad_sort.text

        missing_batch = await client.post(
            "/api/v1/chat/sessions/batch-delete",
            headers=headers,
            json={
                "ids": [
                    created_ids[0],
                    "00000000-0000-0000-0000-000000000000",
                ]
            },
        )
        assert missing_batch.status_code == 404, missing_batch.text
        still = await client.get(
            f"/api/v1/chat/sessions/{created_ids[0]}",
            headers=headers,
        )
        assert still.status_code == 200, still.text

        deleted = await client.post(
            "/api/v1/chat/sessions/batch-delete",
            headers=headers,
            json={"ids": created_ids},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == 3
        for sid in created_ids:
            gone = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            assert gone.status_code == 404, gone.text


@pytest.mark.asyncio
async def test_session_model_filter_and_summary_stats(
    env: dict[str, str], api_base: str
) -> None:
    """模型精确筛选 + 列表摘要（消息条数 / 最近一条正文）。

    消息直接写库而不是跑一轮真实对话：这里验的是列表聚合口径，
    不是模型输出；用真实对话会让断言依赖上游。
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.models_orm.chat import ChatMessage

    long_answer = "根因是连接池耗尽，建议先扩池再排查慢查询。" * 20

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        models = await client.get("/api/v1/aiops/models", headers=headers)
        if models.status_code != 200:
            pytest.skip("无可用模型")
        try:
            provider, model = _pick_model(models.json())
        except LookupError as exc:
            pytest.skip(str(exc))

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        cr = await client.post(
            "/api/v1/chat/sessions",
            headers=headers,
            json={
                "title": f"stats-{stamp}",
                "provider": provider,
                "model": model,
                "agent_id": "default",
            },
        )
        assert cr.status_code == 200, cr.text
        sid = cr.json()["id"]
        # 新建会话没有任何消息：统计不能凭空造数
        assert cr.json()["message_count"] == 0
        assert cr.json()["last_message_preview"] is None

        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        ChatMessage(
                            id=f"{sid}-m1",
                            session_id=sid,
                            role="user",
                            kind="message",
                            content="数据库连接池告警怎么排查？",
                            seq=1,
                        ),
                        # 工具卡没有正文，不能拿来当摘要
                        ChatMessage(
                            id=f"{sid}-m2",
                            session_id=sid,
                            role="tool",
                            kind="tool_card",
                            content=None,
                            seq=2,
                        ),
                        ChatMessage(
                            id=f"{sid}-m3",
                            session_id=sid,
                            role="assistant",
                            kind="message",
                            content=long_answer,
                            seq=3,
                        ),
                    ]
                )
                await db.commit()
        finally:
            await engine.dispose()

        listed = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": f"stats-{stamp}"},
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 1, items
        row = items[0]
        assert row["message_count"] == 3
        # 摘要是最近一条有正文的消息，并按固定长度截断
        assert row["last_message_preview"] == long_answer[:240]
        assert len(row["last_message_preview"]) == 240

        by_model = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": f"stats-{stamp}", "model": model},
        )
        assert by_model.status_code == 200, by_model.text
        assert [i["id"] for i in by_model.json()["items"]] == [sid]

        other_model = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={"q": f"stats-{stamp}", "model": f"{model}-not-exist"},
        )
        assert other_model.status_code == 200, other_model.text
        assert other_model.json()["total"] == 0

        bad_model = await client.get(
            "/api/v1/chat/sessions", headers=headers, params={"model": ""}
        )
        assert bad_model.status_code == 422, bad_model.text

        options = await client.get("/api/v1/chat/sessions/models", headers=headers)
        assert options.status_code == 200, options.text
        assert model in options.json()["items"]

        detail = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["message_count"] == 3

        cleaned = await client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers)
        assert cleaned.status_code == 200, cleaned.text
