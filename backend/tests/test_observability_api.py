"""可观测 API 集成测试：筛选、汇总与分页必须与真实数据一致。"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_observation_filters_and_overview(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        listed = await client.get(
            "/api/v1/observability/turns", params={"limit": 200}, headers=headers
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        items = body["items"]
        total = body["total"]
        assert isinstance(total, int)
        assert len(items) == min(total, 200)

        overview = await client.get("/api/v1/observability/overview", headers=headers)
        assert overview.status_code == 200, overview.text
        payload = overview.json()
        stats = payload["stats"]
        assert stats["total"] == total
        assert stats["error_count"] <= total
        assert isinstance(payload["agents"], list)
        assert isinstance(payload["models"], list)

        if total == 0:
            return

        # 状态筛选：条数与汇总里的失败数一致，且每行状态都是 error。
        errors = await client.get(
            "/api/v1/observability/turns",
            params={"status": "error", "limit": 200},
            headers=headers,
        )
        assert errors.status_code == 200, errors.text
        error_body = errors.json()
        assert error_body["total"] == stats["error_count"]
        assert all(row["status"] == "error" for row in error_body["items"])

        # 延迟筛选：返回行都必须达到阈值。
        slow = await client.get(
            "/api/v1/observability/turns",
            params={"min_duration_ms": 1000, "limit": 200},
            headers=headers,
        )
        assert slow.status_code == 200, slow.text
        slow_body = slow.json()
        assert slow_body["total"] <= total
        assert all(row["duration_ms"] is not None and row["duration_ms"] >= 1000 for row in slow_body["items"])

        # 关键字：命中 trace_id / session_id / request_id，且每条都含关键字。
        keyword = items[0]["session_id"][:8]
        search = await client.get(
            "/api/v1/observability/turns",
            params={"q": keyword, "limit": 200},
            headers=headers,
        )
        assert search.status_code == 200, search.text
        search_body = search.json()
        assert search_body["total"] >= 1
        for row in search_body["items"]:
            haystack = f"{row['trace_id']}{row['session_id']}{row['request_id']}"
            assert keyword in haystack

        # 分页：相邻两页不重复。
        if total >= 2:
            page0 = await client.get(
                "/api/v1/observability/turns",
                params={"limit": 1, "offset": 0},
                headers=headers,
            )
            page1 = await client.get(
                "/api/v1/observability/turns",
                params={"limit": 1, "offset": 1},
                headers=headers,
            )
            assert page0.status_code == 200 and page1.status_code == 200
            assert page0.json()["items"][0]["trace_id"] != page1.json()["items"][0]["trace_id"]

        # 非法状态必须显式失败，不能静默返回空列表。
        bad = await client.get(
            "/api/v1/observability/turns", params={"status": "bogus"}, headers=headers
        )
        assert bad.status_code == 400
