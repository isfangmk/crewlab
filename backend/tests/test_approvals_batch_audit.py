"""审批决策契约：批准不要求原因，拒绝必须填写原因。"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_approve_without_reason_and_reject_with_reason(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        # 批准必须带 body（reason 可为空），缺少 body 由框架拒绝。
        missing = await client.post(
            "/api/v1/approvals/nonexistent/approve",
            headers=headers,
        )
        assert missing.status_code == 422

        # 批准不要求原因：空 reason 直达单据查找，不存在即 404。
        empty = await client.post(
            "/api/v1/approvals/nonexistent/approve",
            headers=headers,
            json={"reason": ""},
        )
        assert empty.status_code == 404

        # 拒绝原因必填。
        reject_empty = await client.post(
            "/api/v1/approvals/nonexistent/reject",
            headers=headers,
            json={"reason": ""},
        )
        assert reject_empty.status_code == 400

        batch = await client.post(
            "/api/v1/approvals/batch",
            headers=headers,
            json={"ids": [], "confirmed": True, "reason": "ok"},
        )
        assert batch.status_code == 400


@pytest.mark.asyncio
async def test_list_approvals_pagination_and_time_filter(env: dict[str, str], api_base: str) -> None:
    """列表返回分页信息与状态分布；时间过滤与非法入参按契约处理。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get("/api/v1/approvals", headers=headers, params={"limit": 1, "offset": 0})
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body["items"], list)
        assert len(body["items"]) <= 1
        assert isinstance(body["total"], int)
        assert body["total"] >= len(body["items"])
        # 状态分布覆盖全部终态，供顶部指标卡渲染。
        assert set(body["counts"]) == {
            "pending",
            "approved",
            "executed",
            "rejected",
            "failed",
            "expired",
        }
        assert all(isinstance(v, int) for v in body["counts"].values())

        # 未来起始时间不可能命中任何审批单，但响应结构必须完整。
        empty = await client.get(
            "/api/v1/approvals",
            headers=headers,
            params={"created_from": "2999-01-01T00:00:00+00:00"},
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert set(empty.json()["counts"].values()) == {0}

        # 时间区间倒置由服务层拒绝。
        inverted = await client.get(
            "/api/v1/approvals",
            headers=headers,
            params={
                "created_from": "2026-01-02T00:00:00+00:00",
                "created_to": "2026-01-01T00:00:00+00:00",
            },
        )
        assert inverted.status_code == 400

        # 未知状态不是合法筛选值。
        bad_status = await client.get(
            "/api/v1/approvals", headers=headers, params={"status": "unknown"}
        )
        assert bad_status.status_code == 400


@pytest.mark.asyncio
async def test_list_approvals_sort_dir(env: dict[str, str], api_base: str) -> None:
    """列表只按提交时间排序：asc / desc 命中同一批记录且方向相反，非法方向拒绝。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        desc = await client.get(
            "/api/v1/approvals", headers=headers, params={"sort_dir": "desc", "limit": 200}
        )
        asc = await client.get(
            "/api/v1/approvals", headers=headers, params={"sort_dir": "asc", "limit": 200}
        )
        assert desc.status_code == 200
        assert asc.status_code == 200
        assert desc.json()["total"] == asc.json()["total"]

        desc_times = [datetime.fromisoformat(item["created_at"]) for item in desc.json()["items"]]
        asc_times = [datetime.fromisoformat(item["created_at"]) for item in asc.json()["items"]]
        assert desc_times == sorted(desc_times, reverse=True)
        assert asc_times == sorted(asc_times)

        bad = await client.get(
            "/api/v1/approvals", headers=headers, params={"sort_dir": "sideways"}
        )
        assert bad.status_code == 400
