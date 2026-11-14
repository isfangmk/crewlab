"""助理配置版本快照与回滚：真实服务集成。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _payload(name: str, *, system_prompt: str) -> dict:
    return {
        "name": name,
        "display_name": "版本测试助理",
        "description": "版本测试助理",
        "when_to_use": "仅测试",
        "system_prompt": system_prompt,
        "critical_reminder": "",
        "allowed_tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 0,
        "output_format": "free",
        "examples": [],
        "knowledge_scope": {"paths": [], "doc_ids": []},
    }


@pytest.mark.asyncio
async def test_agent_version_snapshot_and_rollback(env: dict[str, str], api_base: str) -> None:
    """创建=版本 1、更新=版本 2、回滚=版本 3 且字段回到版本 1。"""

    name = f"ver-agent-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/agents/custom",
            headers=headers,
            json=_payload(name, system_prompt="第一版提示。"),
        )
        assert created.status_code == 200, created.text
        try:
            assert created.json()["version"] == 1

            updated = await client.patch(
                f"/api/v1/agents/custom/{name}",
                headers=headers,
                json=_payload(name, system_prompt="第二版提示。"),
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["version"] == 2

            versions = await client.get(f"/api/v1/agents/custom/{name}/versions", headers=headers)
            assert versions.status_code == 200, versions.text
            items = versions.json()["items"]
            assert [v["version"] for v in items] == [2, 1]

            v1 = await client.get(f"/api/v1/agents/custom/{name}/versions/1", headers=headers)
            assert v1.status_code == 200, v1.text
            assert v1.json()["snapshot"]["system_prompt"] == "第一版提示。"

            rolled = await client.post(
                f"/api/v1/agents/custom/{name}/rollback",
                headers=headers,
                json={"version": 1},
            )
            assert rolled.status_code == 200, rolled.text
            assert rolled.json()["system_prompt"] == "第一版提示。"
            assert rolled.json()["version"] == 3

            versions = await client.get(f"/api/v1/agents/custom/{name}/versions", headers=headers)
            items = versions.json()["items"]
            # 回滚也产生一版，并记录来源版本
            assert items[0]["version"] == 3
            assert items[0]["restored_from_version"] == 1

            current = await client.get(f"/api/v1/agents/{name}", headers=headers)
            assert current.json()["system_prompt"] == "第一版提示。"

            # 不存在的版本显式 404
            missing = await client.get(
                f"/api/v1/agents/custom/{name}/versions/99", headers=headers
            )
            assert missing.status_code == 404, missing.text
        finally:
            deleted = await client.delete(f"/api/v1/agents/custom/{name}", headers=headers)
            assert deleted.status_code == 200, deleted.text
