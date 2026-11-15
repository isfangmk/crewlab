"""评测集：CRUD/校验、真实会话运行、快照冻结与基线对比。

运行测试会真实调用默认助理模型（两个短用例、两次运行），结束后清理
评测集、评测会话、消息与观测记录，避免污染本机数据。
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.approval import Approval
from app.models.attachment import ChatAttachment
from app.models.chat import ChatMessage, ChatSession
from app.models.observation import ObservationToolCall, ObservationTurn
from tests.conftest import login_access_token


async def _wait_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_uid: str,
    *,
    timeout: float = 240.0,
) -> dict:
    """轮询运行详情直到结束；超时显式失败，避免测试悬挂。"""

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/v1/evals/runs/{run_uid}", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(2)
    pytest.fail(f"评测运行超时: {run_uid}")


async def _cleanup_sessions(session_ids: list[str]) -> None:
    """清理评测创建的会话、消息、审批与观测链。"""

    if not session_ids:
        return
    async with SessionLocal() as db:
        trace_ids = list(
            (
                await db.scalars(
                    select(ObservationTurn.id).where(
                        ObservationTurn.session_id.in_(session_ids)
                    )
                )
            ).all()
        )
        if trace_ids:
            await db.execute(
                delete(ObservationToolCall).where(
                    ObservationToolCall.trace_id.in_(trace_ids)
                )
            )
        await db.execute(
            delete(ObservationTurn).where(ObservationTurn.session_id.in_(session_ids))
        )
        await db.execute(
            delete(Approval).where(Approval.session_id.in_(session_ids))
        )
        await db.execute(
            delete(ChatAttachment).where(
                ChatAttachment.session_id.in_(session_ids)
            )
        )
        await db.execute(
            delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids))
        )
        await db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))
        await db.commit()


def _case_body(name: str, prompt: str, keywords: list[str]) -> dict:
    return {
        "name": name,
        "input": prompt,
        "expected_keywords": keywords,
        "forbidden_keywords": [],
        "expected_tools": [],
        "forbidden_tools": [],
        "max_duration_ms": None,
        "max_total_tokens": None,
        "enabled": True,
    }


async def test_eval_dataset_and_case_crud(env: dict[str, str], api_base: str) -> None:
    """名称唯一、断言归一化、部分更新保留旧值，删除后详情 404。"""

    tag = f"eval-crud-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        dataset_id: int | None = None
        case_id: int | None = None
        try:
            created = await client.post(
                "/api/v1/evals/datasets",
                headers=headers,
                json={"name": tag, "description": " 回归用例 "},
            )
            assert created.status_code == 200, created.text
            body = created.json()
            dataset_id = body["id"]
            assert body["description"] == "回归用例"

            duplicate = await client.post(
                "/api/v1/evals/datasets",
                headers=headers,
                json={"name": f"  {tag}  ", "description": ""},
            )
            assert duplicate.status_code == 400, duplicate.text
            assert "已存在" in duplicate.text

            case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={
                    **_case_body(
                        " 归一化用例 ",
                        "  只回复：收到  ",
                        [" 收到 ", "收到", "ok"],
                    ),
                    "forbidden_keywords": [" 报错 "],
                    "max_duration_ms": 60000,
                },
            )
            assert case.status_code == 200, case.text
            case_body = case.json()
            case_id = case_body["id"]
            assert case_body["name"] == "归一化用例"
            assert case_body["input"] == "只回复：收到"
            # 去空白 + 去重保序
            assert case_body["expected_keywords"] == ["收到", "ok"]
            assert case_body["forbidden_keywords"] == ["报错"]

            blank = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json=_case_body("空输入", "   ", []),
            )
            assert blank.status_code in (400, 422), blank.text

            negative = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={**_case_body("负数阈值", "你好", []), "max_duration_ms": -1},
            )
            assert negative.status_code == 422, negative.text

            # 只改名称：输入、断言、阈值必须保留
            patched = await client.patch(
                f"/api/v1/evals/cases/{case_id}",
                headers=headers,
                json={"name": "改名后的用例"},
            )
            assert patched.status_code == 200, patched.text
            updated = patched.json()
            assert updated["name"] == "改名后的用例"
            assert updated["input"] == "只回复：收到"
            assert updated["expected_keywords"] == ["收到", "ok"]
            assert updated["max_duration_ms"] == 60000

            cleared = await client.patch(
                f"/api/v1/evals/cases/{case_id}",
                headers=headers,
                json={"max_duration_ms": None},
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["max_duration_ms"] is None

            detail = await client.get(
                f"/api/v1/evals/datasets/{dataset_id}", headers=headers
            )
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert len(detail_body["cases"]) == 1
            assert detail_body["cases"][0]["name"] == "改名后的用例"
            assert detail_body["runs"] == []

            listed = await client.get("/api/v1/evals/datasets", headers=headers)
            assert listed.status_code == 200, listed.text
            row = next(
                item for item in listed.json()["items"] if item["id"] == dataset_id
            )
            assert row["case_count"] == 1
            assert row["enabled_case_count"] == 1

            removed = await client.delete(
                f"/api/v1/evals/cases/{case_id}", headers=headers
            )
            assert removed.status_code == 200, removed.text
            case_id = None

            gone = await client.delete(
                f"/api/v1/evals/datasets/{dataset_id}", headers=headers
            )
            assert gone.status_code == 200, gone.text
            dataset_id = None

            missing = await client.get("/api/v1/evals/datasets/99999999", headers=headers)
            assert missing.status_code == 404, missing.text
        finally:
            if case_id is not None:
                await client.delete(f"/api/v1/evals/cases/{case_id}", headers=headers)
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )


async def test_eval_real_run_and_compare(env: dict[str, str], api_base: str) -> None:
    """两次真实运行：第二次修正断言后对比，验证改善与快照冻结。"""

    tag = f"eval-run-{uuid.uuid4().hex[:8]}"
    session_ids: list[str] = []
    dataset_id: int | None = None
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            created = await client.post(
                "/api/v1/evals/datasets",
                headers=headers,
                json={"name": tag, "description": "真实会话回归"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]

            pass_case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json=_case_body(
                    "命中关键词",
                    "请只输出这两个字，不要解释：收到",
                    ["收到"],
                ),
            )
            assert pass_case.status_code == 200, pass_case.text
            fail_case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json=_case_body(
                    "故意失败",
                    "请只输出这两个字，不要解释：收到",
                    ["绝对不存在的关键词ZZZ"],
                ),
            )
            assert fail_case.status_code == 200, fail_case.text
            fail_case_id = fail_case.json()["id"]

            first = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/runs",
                headers=headers,
                json={"agent_id": "default", "provider": "", "model": ""},
            )
            assert first.status_code == 200, first.text
            first_run = first.json()
            assert first_run["status"] == "running"
            assert first_run["model"] != ""
            first_detail = await _wait_run(client, headers, first_run["run_uid"])
            assert first_detail["status"] == "completed", first_detail
            assert first_detail["total_cases"] == 2
            assert first_detail["passed_cases"] == 1
            assert first_detail["failed_cases"] == 1
            results = {item["case_name"]: item for item in first_detail["results"]}
            assert results["命中关键词"]["status"] == "passed"
            assert results["故意失败"]["status"] == "failed"
            assert any(
                "缺少期望关键词" in reason
                for reason in results["故意失败"]["failure_reasons"]
            )
            snapshot = {
                item["name"]: item for item in first_detail["cases_snapshot"]
            }
            assert snapshot["故意失败"]["expected_keywords"] == [
                "绝对不存在的关键词ZZZ"
            ]
            for item in first_detail["results"]:
                assert item["session_id"] != ""
                session_ids.append(item["session_id"])

            # 修正失败用例的断言，第二次运行应全通过
            fixed = await client.patch(
                f"/api/v1/evals/cases/{fail_case_id}",
                headers=headers,
                json={"expected_keywords": ["收到"]},
            )
            assert fixed.status_code == 200, fixed.text

            second = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/runs",
                headers=headers,
                json={"agent_id": "default", "provider": "", "model": ""},
            )
            assert second.status_code == 200, second.text
            second_run = second.json()
            second_detail = await _wait_run(client, headers, second_run["run_uid"])
            assert second_detail["status"] == "completed", second_detail
            assert second_detail["passed_cases"] == 2
            assert second_detail["failed_cases"] == 0
            for item in second_detail["results"]:
                assert item["status"] == "passed"
                assert item["session_id"] != ""
                session_ids.append(item["session_id"])

            compared = await client.get(
                f"/api/v1/evals/runs/{second_run['run_uid']}/compare",
                headers=headers,
                params={"baseline": first_run["run_uid"]},
            )
            assert compared.status_code == 200, compared.text
            comparison = compared.json()
            assert comparison["summary"]["baseline_passed"] == 1
            assert comparison["summary"]["current_passed"] == 2
            assert comparison["summary"]["improved"] == 1
            assert comparison["summary"]["regressed"] == 0
            changed = next(
                item
                for item in comparison["cases"]
                if item["case_name"] == "故意失败"
            )
            assert changed["change"] == "improved"
            assert changed["baseline_status"] == "failed"
            assert changed["current_status"] == "passed"

            same = await client.get(
                f"/api/v1/evals/runs/{second_run['run_uid']}/compare",
                headers=headers,
                params={"baseline": second_run["run_uid"]},
            )
            assert same.status_code == 400, same.text
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)
