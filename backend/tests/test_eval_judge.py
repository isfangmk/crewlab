"""LLM 评分：解析与配置校验单测 + 配置读写 + 真实裁判打分链路。

真实裁判复用评测运行（一条短用例），评测模型不可达时该断言显式 skip；
judge_result 评分失败会落 parse_error，断言只要求契约字段齐全。
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.services.eval import eval_judge_service
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


def test_parse_judge_response_contract() -> None:
    """合法返回解析成维度分，平均分口径稳定。"""

    dims = eval_judge_service.parse_judge_response(
        '{"dimensions": [{"dimension": "正确性", "score": 4, "reason": "准确"}]}'
    )
    assert dims[0].dimension == "正确性"
    assert dims[0].score == 4.0
    assert eval_judge_service.average_score(dims) == 4.0


def test_parse_judge_response_rejects_broken_payloads() -> None:
    """畸形返回显式抛错，由调用方落 parse_error，不静默吞。"""

    with pytest.raises(Exception):
        eval_judge_service.parse_judge_response("不是 JSON")
    with pytest.raises(ValueError):
        eval_judge_service.parse_judge_response('{"dimensions": []}')
    with pytest.raises(ValueError):
        eval_judge_service.parse_judge_response(
            '{"dimensions": [{"dimension": "x", "score": 9, "reason": ""}]}'
        )


def test_validate_judge_fields_rejects_broken_config() -> None:
    """裁判模型给一半、阈值越界都直接失败。"""

    with pytest.raises(ValueError):
        eval_judge_service.validate_judge_fields(
            enabled=True,
            rubric="",
            judge_provider="",
            judge_model="only-model",
            pass_score=3.0,
        )
    with pytest.raises(ValueError):
        eval_judge_service.validate_judge_fields(
            enabled=True,
            rubric="",
            judge_provider="p",
            judge_model="m",
            pass_score=6.0,
        )


async def test_judge_config_and_real_scoring(env: dict[str, str], api_base: str) -> None:
    """配置读写走真实 API，跑一条短用例验证 judge 分数落库可查。"""

    tag = f"eval-judge-{uuid.uuid4().hex[:8]}"
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
                json={"name": tag, "description": "评分链路"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]

            default_cfg = await client.get(
                f"/api/v1/evals/datasets/{dataset_id}/judge-config",
                headers=headers,
            )
            assert default_cfg.status_code == 200, default_cfg.text
            assert default_cfg.json()["enabled"] is False

            saved = await client.put(
                f"/api/v1/evals/datasets/{dataset_id}/judge-config",
                headers=headers,
                json={
                    "enabled": True,
                    "rubric": "按正确性打分",
                    "judge_provider": "",
                    "judge_model": "",
                    "pass_score": 2.5,
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["pass_score"] == 2.5
            assert saved.json()["enabled"] is True

            half = await client.put(
                f"/api/v1/evals/datasets/{dataset_id}/judge-config",
                headers=headers,
                json={
                    "enabled": True,
                    "rubric": "",
                    "judge_provider": "",
                    "judge_model": "only-model",
                    "pass_score": 3.0,
                },
            )
            assert half.status_code == 400, half.text

            case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={
                    "name": "问好",
                    "input": "请只输出这两个字，不要解释：收到",
                    "expected_keywords": ["收到"],
                    "forbidden_keywords": [],
                    "expected_tools": [],
                    "forbidden_tools": [],
                },
            )
            assert case.status_code == 200, case.text
            result_id_placeholder = case.json()["id"]

            run = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/runs",
                headers=headers,
                json={"agent_id": "default", "provider": "", "model": ""},
            )
            assert run.status_code == 200, run.text
            run_uid = run.json()["run_uid"]
            detail = await _wait_run(client, headers, run_uid)
            assert detail["status"] == "completed", detail
            for item in detail["results"]:
                if item["session_id"]:
                    session_ids.append(item["session_id"])

            results = detail["results"]
            if not results or any(item["status"] == "error" for item in results):
                pytest.skip("评测模型不可达，跳过真实裁判断言")
            result_id = results[0]["id"]

            scores = await client.get(
                f"/api/v1/evals/runs/{run_uid}/results/{result_id}/judge-scores",
                headers=headers,
            )
            assert scores.status_code == 200, scores.text
            items = scores.json()["items"]
            assert items, "启用评分后应有 judge 分数（含 parse_error）"
            for item in items:
                assert item["dimension"] != ""
                assert isinstance(item["score"], (int, float))
                assert item["judge_model"] != ""

            missing = await client.get(
                f"/api/v1/evals/runs/{run_uid}/results/ffffffffffffffffffffffffffffffff/judge-scores",
                headers=headers,
            )
            assert missing.status_code == 404
            assert result_id_placeholder != ""
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)


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
