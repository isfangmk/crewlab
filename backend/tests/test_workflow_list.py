"""工作流列表：搜索/状态筛选、分页，以及每项的最近一次运行状态（真实后端 + 真实 PG）。

列表走服务端分页：total 是筛选后的总数，items 是本页数据；
每项带 last_run（最近一次运行的 run_uid / status / created_at），从未运行时为 null。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _definition() -> dict:
    """最小可保存图：只有开始与结束。"""

    return {
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "label": "开始",
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
async def test_list_workflows_filters_and_paginates(
    env: dict[str, str], api_base: str
) -> None:
    prefix = f"it-list-{uuid.uuid4().hex[:8]}"
    names = [f"{prefix}-1", f"{prefix}-2", f"{prefix}-3"]
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created: list[int] = []
        try:
            for name in names:
                response = await client.post(
                    "/api/v1/workflows",
                    headers=headers,
                    json={"name": name, "description": "列表分页用", "definition": _definition()},
                )
                assert response.status_code == 200, response.text
                created.append(response.json()["id"])
            # 第 3 个发布，用来验证状态筛选
            published = await client.post(
                f"/api/v1/workflows/{created[2]}/publish", headers=headers
            )
            assert published.status_code == 200, published.text

            # 关键字筛选：只命中这 3 条，且 total 是筛选后的总数
            first_page = await client.get(
                "/api/v1/workflows",
                headers=headers,
                params={"q": prefix, "limit": 2, "offset": 0},
            )
            assert first_page.status_code == 200, first_page.text
            body = first_page.json()
            assert body["total"] == 3
            assert len(body["items"]) == 2
            # 本页数据带结构规模与 last_run，未运行过的工作流 last_run 为 null
            assert all("preview" not in item for item in body["items"])
            assert all(item["last_run"] is None for item in body["items"])

            second_page = await client.get(
                "/api/v1/workflows",
                headers=headers,
                params={"q": prefix, "limit": 2, "offset": 2},
            )
            assert second_page.status_code == 200, second_page.text
            tail = second_page.json()
            assert tail["total"] == 3
            assert len(tail["items"]) == 1
            page_ids = {item["id"] for item in body["items"]}
            tail_ids = {item["id"] for item in tail["items"]}
            assert page_ids.isdisjoint(tail_ids)
            assert page_ids | tail_ids == set(created)

            # 状态筛选：草稿 2 条，已发布 1 条
            drafts = await client.get(
                "/api/v1/workflows",
                headers=headers,
                params={"q": prefix, "status": "draft", "limit": 20},
            )
            assert drafts.status_code == 200, drafts.text
            assert drafts.json()["total"] == 2
            assert [item["status"] for item in drafts.json()["items"]] == ["draft", "draft"]

            online = await client.get(
                "/api/v1/workflows",
                headers=headers,
                params={"q": prefix, "status": "published", "limit": 20},
            )
            assert online.status_code == 200, online.text
            assert online.json()["total"] == 1
            assert online.json()["items"][0]["id"] == created[2]

            # 非法筛选值直接拒绝，不静默忽略
            bad = await client.get(
                "/api/v1/workflows", headers=headers, params={"status": "archived"}
            )
            assert bad.status_code == 400, bad.text

            # 真正跑一次：列表的 last_run 跟随到这次运行的 uid 与状态
            run = await client.post(
                f"/api/v1/workflows/{created[0]}/runs",
                headers=headers,
                json={"values": {"topic": "磁盘告警"}},
            )
            assert run.status_code == 200, run.text
            run_uid = run.json()["run_uid"]
            after_run = await client.get(
                "/api/v1/workflows",
                headers=headers,
                params={"q": prefix, "status": "draft", "limit": 20},
            )
            assert after_run.status_code == 200, after_run.text
            latest = {
                item["id"]: item["last_run"] for item in after_run.json()["items"]
            }
            assert latest[created[0]]["run_uid"] == run_uid
            assert latest[created[0]]["status"] in {"running", "succeeded"}
            assert latest[created[1]] is None
        finally:
            for workflow_id in created:
                await client.delete(
                    f"/api/v1/workflows/{workflow_id}", headers=headers
                )
