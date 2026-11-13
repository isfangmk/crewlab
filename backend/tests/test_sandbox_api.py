"""沙盒 API 集成测试：列表、详情、概览与手动试跑。

覆盖范围：
  - 未登录访问被拒；
  - 列表按来源 / 状态筛选，且 total 与返回条数一致；
  - 详情能按 id 取回单条记录；
  - 概览统计与列表口径一致；
  - 非法筛选值返回 400，不是 500；
  - 手动试跑落一条 manual 来源的记录。
"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_sandbox_api_requires_auth() -> None:
    """沙盒接口必须登录才能用。"""

    async with httpx.AsyncClient(base_url="http://127.0.0.1:18080", timeout=30.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        resp = await client.get("/api/v1/sandbox/executions")
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_sandbox_list_detail_and_summary(env: dict[str, str], api_base: str) -> None:
    """列表、详情、概览三者口径要一致。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        listed = await client.get(
            "/api/v1/sandbox/executions", params={"limit": 200}, headers=headers
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        items = body["items"]
        total = body["total"]
        assert isinstance(total, int)
        assert len(items) == min(total, 200)
        if items:
            assert "code" in items[0]
            assert "stdout" in items[0]
            assert "duration_ms" in items[0]

        summary = await client.get("/api/v1/sandbox/summary", headers=headers)
        assert summary.status_code == 200, summary.text
        stats = summary.json()
        assert stats["total"] == total
        assert isinstance(stats["by_source"], dict)
        assert isinstance(stats["by_status"], dict)

        if items:
            detail = await client.get(
                f"/api/v1/sandbox/executions/{items[0]['id']}", headers=headers
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == items[0]["id"]


@pytest.mark.asyncio
async def test_sandbox_list_rejects_unknown_filter(env: dict[str, str], api_base: str) -> None:
    """非法筛选值返回 400，不能是 500。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/api/v1/sandbox/executions", params={"source": "未知来源"}, headers=headers
        )
        assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_sandbox_detail_404_for_unknown_id(env: dict[str, str], api_base: str) -> None:
    """查不存在的记录返回 404。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/api/v1/sandbox/executions/not-exist-id", headers=headers
        )
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_sandbox_manual_run_is_recorded(env: dict[str, str], api_base: str) -> None:
    """手动试跑要落一条 manual 来源的记录。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            "/api/v1/sandbox/run",
            json={"code": "print('api-manual')"},
            headers=headers,
        )
        if resp.status_code == 503:
            pytest.skip("沙盒环境不可用")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["status"] == "succeeded"
        assert "api-manual" in payload["stdout"]
        assert payload["execution_id"]

        detail = await client.get(
            f"/api/v1/sandbox/executions/{payload['execution_id']}", headers=headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["source"] == "manual"
