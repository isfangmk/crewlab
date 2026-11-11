"""工作流草稿与发布：只有发布升版本，草稿保存只改内容（真实后端 + 真实 PG）。

画布编辑一律落草稿：保存不碰 status / version；「发布」才是版本 +1 的时机。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _definition(marked_label: str) -> dict:
    """最小可跑图：开始 → 结束。marked_label 用来制造两次不同的草稿内容。"""

    return {
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "label": marked_label,
                "variables": [
                    {
                        "name": "topic",
                        "type": "string",
                        "description": "",
                        "required": True,
                        "default": None,
                    }
                ],
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end", "route": ""}],
    }


@pytest.mark.asyncio
async def test_publish_is_the_only_version_bump(
    env: dict[str, str], api_base: str
) -> None:
    name = f"it-workflow-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={"name": name, "description": "", "definition": _definition("v1")},
        )
        assert created.status_code == 200, created.text
        workflow_id = created.json()["id"]
        try:
            assert (created.json()["status"], created.json()["version"]) == ("draft", 1)

            # 草稿保存：内容更新，状态仍是草稿，版本不动
            saved = await client.put(
                f"/api/v1/workflows/{workflow_id}",
                headers=headers,
                json={
                    "name": name,
                    "description": "改一版说明",
                    "definition": _definition("v2"),
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["status"] == "draft"
            assert saved.json()["version"] == 1
            assert saved.json()["description"] == "改一版说明"
            assert saved.json()["definition"]["nodes"][0]["label"] == "v2"

            # 首次发布：初始版本号就是第一版，不额外 +1
            published = await client.post(
                f"/api/v1/workflows/{workflow_id}/publish", headers=headers
            )
            assert published.status_code == 200, published.text
            assert published.json()["status"] == "published"
            assert published.json()["version"] == 1

            # 发布后再改：回到草稿，版本仍停在 1
            edited = await client.put(
                f"/api/v1/workflows/{workflow_id}",
                headers=headers,
                json={
                    "name": name,
                    "description": "改一版说明",
                    "definition": _definition("v3"),
                },
            )
            assert edited.status_code == 200, edited.text
            assert (edited.json()["status"], edited.json()["version"]) == ("draft", 1)

            # 再次发布：这才是 v2
            republished = await client.post(
                f"/api/v1/workflows/{workflow_id}/publish", headers=headers
            )
            assert republished.status_code == 200, republished.text
            assert republished.json()["status"] == "published"
            assert republished.json()["version"] == 2
        finally:
            await client.delete(f"/api/v1/workflows/{workflow_id}", headers=headers)
