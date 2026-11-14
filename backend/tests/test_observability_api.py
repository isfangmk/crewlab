"""可观测 API 集成测试：筛选、汇总与分页必须与真实数据一致。"""

from __future__ import annotations

from datetime import datetime

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
        for row in items:
            # 首字延迟是新增契约：未产出正文的回合显式为空，不能缺字段
            assert "first_token_ms" in row
            first_token_ms = row["first_token_ms"]
            assert first_token_ms is None or first_token_ms >= 0
            if first_token_ms is not None and row["duration_ms"] is not None:
                assert first_token_ms <= row["duration_ms"]

        overview = await client.get("/api/v1/observability/overview", headers=headers)
        assert overview.status_code == 200, overview.text
        payload = overview.json()
        stats = payload["stats"]
        assert stats["total"] == total
        assert stats["error_count"] <= total
        assert "avg_first_token_ms" in stats
        assert "p95_first_token_ms" in stats
        assert stats["avg_first_token_ms"] is None or stats["avg_first_token_ms"] >= 0
        assert stats["p95_first_token_ms"] is None or stats["p95_first_token_ms"] >= 0
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

        # 耗时倒序：非空耗时严格递减，进行中的空耗时始终垫底。
        slow = await client.get(
            "/api/v1/observability/turns",
            params={"sort_by": "duration_ms", "sort_order": "desc", "limit": 200},
            headers=headers,
        )
        assert slow.status_code == 200, slow.text
        durations = [row["duration_ms"] for row in slow.json()["items"]]
        non_null_durations = [value for value in durations if value is not None]
        assert non_null_durations == sorted(non_null_durations, reverse=True)
        assert durations[: len(non_null_durations)] == non_null_durations
        assert all(value is None for value in durations[len(non_null_durations) :])

        # 时间升序：解析后严格非递减。
        by_time = await client.get(
            "/api/v1/observability/turns",
            params={"sort_by": "started_at", "sort_order": "asc", "limit": 200},
            headers=headers,
        )
        assert by_time.status_code == 200, by_time.text
        times = [
            datetime.fromisoformat(row["started_at"])
            for row in by_time.json()["items"]
            if row["started_at"] is not None
        ]
        assert times == sorted(times)

        # 非法排序字段 / 方向必须显式失败。
        bad_sort = await client.get(
            "/api/v1/observability/turns",
            params={"sort_by": "agent_id"},
            headers=headers,
        )
        assert bad_sort.status_code == 400
        bad_order = await client.get(
            "/api/v1/observability/turns",
            params={"sort_order": "sideways"},
            headers=headers,
        )
        assert bad_order.status_code == 400

        # 非法状态必须显式失败，不能静默返回空列表。
        bad = await client.get(
            "/api/v1/observability/turns", params={"status": "bogus"}, headers=headers
        )
        assert bad.status_code == 400
