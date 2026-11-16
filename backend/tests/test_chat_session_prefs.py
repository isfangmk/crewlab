"""「思考 / 联网搜索」开关随会话持久化：建会话初值、PATCH 写回、GET 同步。"""

from __future__ import annotations

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
async def test_session_prefs_persist_and_sync(env: dict[str, str], api_base: str) -> None:
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

        sid = ""
        sid_default = ""
        try:
            # create 带开关初值 → 落库并从 GET 读回（换页面打开即同步的取数路径）
            cr = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": "prefs-test-session",
                    "provider": provider,
                    "model": model,
                    "agent_id": "default",
                    "thinking_enabled": True,
                    "web_search_enabled": False,
                },
            )
            assert cr.status_code == 200, cr.text
            body = cr.json()
            sid = body["id"]
            assert body["thinking_enabled"] is True
            assert body["web_search_enabled"] is False

            got = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            assert got.status_code == 200, got.text
            assert got.json()["thinking_enabled"] is True
            assert got.json()["web_search_enabled"] is False

            # 不传开关字段时默认关（extra=forbid 下字段可省）
            cr_default = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": "prefs-test-default",
                    "provider": provider,
                    "model": model,
                    "agent_id": "default",
                },
            )
            assert cr_default.status_code == 200, cr_default.text
            body_default = cr_default.json()
            sid_default = body_default["id"]
            assert body_default["thinking_enabled"] is False
            assert body_default["web_search_enabled"] is False

            # PATCH 写回：两开关各自更新并从 GET 读回
            patched = await client.patch(
                f"/api/v1/chat/sessions/{sid}/prefs",
                headers=headers,
                json={"thinking_enabled": False, "web_search_enabled": True},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["thinking_enabled"] is False
            assert patched.json()["web_search_enabled"] is True

            got_after = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers)
            assert got_after.status_code == 200, got_after.text
            assert got_after.json()["thinking_enabled"] is False
            assert got_after.json()["web_search_enabled"] is True

            # 只更新一个开关不碰另一个
            partial = await client.patch(
                f"/api/v1/chat/sessions/{sid}/prefs",
                headers=headers,
                json={"thinking_enabled": True},
            )
            assert partial.status_code == 200, partial.text
            assert partial.json()["thinking_enabled"] is True
            assert partial.json()["web_search_enabled"] is True

            # 两者都不传 → 400
            empty = await client.patch(
                f"/api/v1/chat/sessions/{sid}/prefs",
                headers=headers,
                json={},
            )
            assert empty.status_code == 400, empty.text

            # 未知会话 → 404
            missing = await client.patch(
                "/api/v1/chat/sessions/00000000-0000-0000-0000-000000000000/prefs",
                headers=headers,
                json={"thinking_enabled": True},
            )
            assert missing.status_code == 404, missing.text
        finally:
            # 测试会话数据必须清理
            for doomed in (sid, sid_default):
                if doomed:
                    await client.delete(f"/api/v1/chat/sessions/{doomed}", headers=headers)
