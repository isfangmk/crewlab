"""评测门禁：基线对比 + 回归上限 / 通过率下限判定。

需要两次真实评测运行（基线一次、回归一次）；把用例断言改坏制造回归，
验证门禁 fail 的原因清单与放宽阈值后的 pass，数据随评测集删除级联清理。
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

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
    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.models.approval import Approval
    from app.models.attachment import ChatAttachment
    from app.models.chat import ChatMessage, ChatSession
    from app.models.observation import ObservationToolCall, ObservationTurn

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
        await db.execute(delete(Approval).where(Approval.session_id.in_(session_ids)))
        await db.execute(
            delete(ChatAttachment).where(ChatAttachment.session_id.in_(session_ids))
        )
        await db.execute(
            delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids))
        )
        await db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))
        await db.commit()


async def test_gate_regression_thresholds(env: dict[str, str], api_base: str) -> None:
    """制造回归 → 门禁 fail 并列原因；放宽阈值 → 门禁 pass。"""

    tag = f"eval-gate-{uuid.uuid4().hex[:8]}"
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
                json={"name": tag, "description": "门禁判定"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]
            case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={
                    "name": "命中关键词",
                    "input": "请只输出这两个字，不要解释：收到",
                    "expected_keywords": ["收到"],
                    "forbidden_keywords": [],
                    "expected_tools": [],
                    "forbidden_tools": [],
                },
            )
            assert case.status_code == 200, case.text
            case_id = case.json()["id"]

            first = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/runs",
                headers=headers,
                json={"agent_id": "default", "provider": "", "model": ""},
            )
            assert first.status_code == 200, first.text
            first_uid = first.json()["run_uid"]
            first_detail = await _wait_run(client, headers, first_uid)
            assert first_detail["status"] == "completed", first_detail
            for item in first_detail["results"]:
                if item["session_id"]:
                    session_ids.append(item["session_id"])

            # 未设基线时门禁显式失败
            no_baseline = await client.post(
                f"/api/v1/evals/runs/{first_uid}/gate", headers=headers
            )
            assert no_baseline.status_code == 400, no_baseline.text

            configured = await client.patch(
                f"/api/v1/evals/datasets/{dataset_id}",
                headers=headers,
                json={
                    "baseline_run_uid": first_uid,
                    "gate_max_regressions": 0,
                    "gate_min_pass_rate": 0.5,
                },
            )
            assert configured.status_code == 200, configured.text
            assert configured.json()["baseline_run_uid"] == first_uid

            # 基线对自己：无回归，只看通过率
            self_gate = await client.post(
                f"/api/v1/evals/runs/{first_uid}/gate", headers=headers
            )
            assert self_gate.status_code == 200, self_gate.text
            assert self_gate.json()["pass"] is True

            # 把断言改坏制造回归，第二次运行必失败
            broken = await client.patch(
                f"/api/v1/evals/cases/{case_id}",
                headers=headers,
                json={"expected_keywords": ["绝对不存在的关键词ZZZ"]},
            )
            assert broken.status_code == 200, broken.text
            second = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/runs",
                headers=headers,
                json={"agent_id": "default", "provider": "", "model": ""},
            )
            assert second.status_code == 200, second.text
            second_uid = second.json()["run_uid"]
            second_detail = await _wait_run(client, headers, second_uid)
            assert second_detail["status"] == "completed", second_detail
            for item in second_detail["results"]:
                if item["session_id"]:
                    session_ids.append(item["session_id"])
            if any(item["status"] == "error" for item in second_detail["results"]):
                pytest.skip("评测模型不可达，跳过门禁判定断言")

            gate = await client.post(
                f"/api/v1/evals/runs/{second_uid}/gate", headers=headers
            )
            assert gate.status_code == 200, gate.text
            gate_body = gate.json()
            assert gate_body["pass"] is False
            assert gate_body["regressions"] == 1
            assert gate_body["pass_rate"] < 0.5
            assert any("回归" in reason for reason in gate_body["reasons"])
            assert any("通过率" in reason for reason in gate_body["reasons"])
            assert gate_body["regressed_cases"][0]["case_name"] == "命中关键词"

            # 放宽阈值后同一运行判 pass
            relaxed = await client.patch(
                f"/api/v1/evals/datasets/{dataset_id}",
                headers=headers,
                json={"gate_max_regressions": 1, "gate_min_pass_rate": 0.0},
            )
            assert relaxed.status_code == 200, relaxed.text
            gate2 = await client.post(
                f"/api/v1/evals/runs/{second_uid}/gate", headers=headers
            )
            assert gate2.status_code == 200, gate2.text
            assert gate2.json()["pass"] is True

            # 非法阈值与他人运行当基线都显式失败
            bad_threshold = await client.patch(
                f"/api/v1/evals/datasets/{dataset_id}",
                headers=headers,
                json={"gate_min_pass_rate": 1.5},
            )
            assert bad_threshold.status_code == 422, bad_threshold.text
            missing_run = await client.post(
                f"/api/v1/evals/runs/{'0' * 32}/gate", headers=headers
            )
            assert missing_run.status_code == 404, missing_run.text
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)
