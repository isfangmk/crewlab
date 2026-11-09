"""技能目录重载。"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_skills_reload_ok(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post("/api/v1/skills/reload", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] >= 1
        keys = [i["key"] for i in body["items"]]
        assert "knowledge-search" in keys
        item = next(i for i in body["items"] if i["key"] == "knowledge-search")
        assert "tools" in item
        assert item["class"] == "safe"
