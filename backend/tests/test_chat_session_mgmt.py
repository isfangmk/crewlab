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

        # 最近活动列：空会话没有 last_message_at，DESC 下必须排最后而不是顶到第一页
        by_activity = await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
            params={
                "q": f"sort-batch-{stamp}",
                "sort_by": "last_message_at",
                "sort_dir": "desc",
                "limit": 20,
                "offset": 0,
            },
        )
        assert by_activity.status_code == 200, by_activity.text
        assert len(by_activity.json()["items"]) == 3

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
async def test_session_batch_delete_many_keeps_audit_in_range(
    env: dict[str, str], api_base: str
) -> None:
    """批量删除超过 3 个会话：审计不能被拼接过长的 resource_id 撑爆（曾经 4 个就 500）。"""

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
        ids: list[str] = []
        for i in range(6):
            cr = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": f"batch-many-{stamp}-{i}",
                    "provider": provider,
                    "model": model,
                    "agent_id": "default",
                },
            )
            assert cr.status_code == 200, cr.text
            ids.append(cr.json()["id"])

        # 6 个 UUID 拼起来 221 字符，远超 audit_logs.resource_id 的 128
        assert len(",".join(ids)) > 128
        res = await client.post(
            "/api/v1/chat/sessions/batch-delete", headers=headers, json={"ids": ids}
        )
        assert res.status_code == 200, res.text
        assert res.json()["deleted"] == 6
        for sid in ids:
            gone = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            assert gone.status_code == 404, gone.text

        # 审计落库且 id 列表完整记在 detail 里
        audit = await client.get(
            "/api/v1/audit",
            headers=headers,
            params={"action": "session.batch_delete", "limit": 5},
        )
        assert audit.status_code == 200, audit.text
        rows = audit.json()["items"]
        matched = [r for r in rows if r["detail"].get("ids") == ids]
        assert len(matched) == 1, rows


@pytest.mark.asyncio
async def test_session_model_filter_and_summary_stats(
    env: dict[str, str], api_base: str
) -> None:
    """模型精确筛选 + 列表摘要（消息条数 / 工具调用 / 最近活动）。

    消息直接写库而不是跑一轮真实对话：这里验的是列表聚合口径，
    不是模型输出；用真实对话会让断言依赖上游。
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.models_orm.attachment import ChatAttachment
    from app.models_orm.chat import ChatMessage
    from app.models_orm.observation import ObservationTurn
    from app.models_orm.share import ShareToken

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
        # 新建会话没有任何消息 / 观测 / 附件 / 分享：统计不能凭空造数
        assert cr.json()["message_count"] == 0
        assert cr.json()["turn_count"] == 0
        assert cr.json()["tool_call_count"] == 0
        assert cr.json()["skill_call_count"] == 0
        assert cr.json()["last_message_at"] is None
        assert cr.json()["last_message_preview"] is None
        assert cr.json()["total_tokens"] is None
        assert cr.json()["last_turn_status"] is None
        assert cr.json()["pending_approvals"] == 0
        assert cr.json()["attachment_count"] == 0
        assert cr.json()["shared"] is False

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
                        # 工具 / 技能调用与 tokens 只认观测的回合账，不看消息行数
                        ObservationTurn(
                            id=f"{sid}-t1",
                            session_id=sid,
                            agent_id="default",
                            provider=provider,
                            model=model,
                            status="error",
                            input_summary="数据库连接池告警怎么排查？",
                            prompt_tokens=1000,
                            completion_tokens=500,
                            total_tokens=1500,
                            duration_ms=1200,
                            tool_call_count=2,
                            skill_call_count=1,
                        ),
                        ChatAttachment(
                            # id 列是 36 位，直接复用会话 id（一附件一会话）
                            id=sid,
                            session_id=sid,
                            name="alert.png",
                            mime_type="image/png",
                            size=1024,
                            data_url="data:image/png;base64,AAAA",
                        ),
                        ShareToken(
                            session_id=sid,
                            token_hash=f"hash-{sid}",
                            created_by=1,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                        ),
                        # 已过期的分享不算「已分享」：链接已经点不开
                        ShareToken(
                            session_id=sid,
                            token_hash=f"hash-expired-{sid}",
                            created_by=1,
                            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
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
        # 只数正文消息：user + assistant 两条，工具卡不算「消息」
        assert row["message_count"] == 2
        # 轮数按用户提问计，不是消息条数
        assert row["turn_count"] == 1
        assert row["tool_call_count"] == 2
        assert row["skill_call_count"] == 1
        assert row["total_tokens"] == 1500
        assert row["last_turn_status"] == "error"
        assert row["attachment_count"] == 1
        assert row["shared"] is True
        assert row["last_message_at"] is not None
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
        assert detail.json()["message_count"] == 2
        assert detail.json()["turn_count"] == 1
        assert detail.json()["tool_call_count"] == 2

        cleaned = await client.delete(f"/api/v1/chat/sessions/{sid}", headers=headers)
        assert cleaned.status_code == 200, cleaned.text
