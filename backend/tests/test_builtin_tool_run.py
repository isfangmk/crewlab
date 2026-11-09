"""内置工具手动运行。"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_run_query_knowledge(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        catalog = await client.get("/api/v1/tools/builtin", headers=headers)
        assert catalog.status_code == 200, catalog.text
        names = [i["name"] for i in catalog.json()["items"]]
        assert "query_knowledge" in names

        run = await client.post(
            "/api/v1/tools/builtin/query_knowledge/run",
            headers=headers,
            json={"params": {"query": "DNS", "limit": 2}},
        )
        if run.status_code >= 500:
            pytest.skip(f"嵌入/库不可用: {run.text}")
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["tool"] == "query_knowledge"
        assert "result" in body
