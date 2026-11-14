"""评测导出：CSV / JSON 字段完整且脱敏，密钥类内容不带出系统。

需要一条真实评测运行作为导出对象；用例名里埋 sk- 形态的假密钥，
断言导出文本只含遮蔽后的值，数据随评测集删除级联清理。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid

import httpx
import pytest

from tests.conftest import login_access_token

SECRET = "sk-abcdefgh12345XYZ"


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
    from app.models_orm.approval import Approval
    from app.models_orm.attachment import ChatAttachment
    from app.models_orm.chat import ChatMessage, ChatSession
    from app.models_orm.observation import ObservationToolCall, ObservationTurn

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


async def test_export_csv_json_redacted(env: dict[str, str], api_base: str) -> None:
    """JSON / CSV 导出字段完整，假密钥只出现遮蔽形态。"""

    tag = f"eval-exp-{uuid.uuid4().hex[:8]}"
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
                json={"name": tag, "description": "导出脱敏"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]
            case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={
                    "name": f"导出-{SECRET}",
                    "input": "请只输出这两个字，不要解释：收到",
                    "expected_keywords": ["收到"],
                    "forbidden_keywords": [],
                    "expected_tools": [],
                    "forbidden_tools": [],
                },
            )
            assert case.status_code == 200, case.text
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
            if not detail["results"]:
                pytest.skip("评测没有产生结果，跳过导出断言")

            exported = await client.get(
                f"/api/v1/evals/runs/{run_uid}/export",
                headers=headers,
                params={"format": "json"},
            )
            assert exported.status_code == 200, exported.text
            assert "application/json" in exported.headers["content-type"]
            payload = json.loads(exported.text)
            assert payload["run_uid"] == run_uid
            row = payload["results"][0]
            for key in (
                "case_name",
                "status",
                "input",
                "output",
                "failure_reasons",
                "tool_names",
                "duration_ms",
                "trace_id",
                "session_id",
                "created_at",
            ):
                assert key in row, key
            assert SECRET not in exported.text
            assert "sk-abcdefgh***" in exported.text

            as_csv = await client.get(
                f"/api/v1/evals/runs/{run_uid}/export",
                headers=headers,
                params={"format": "csv"},
            )
            assert as_csv.status_code == 200, as_csv.text
            assert as_csv.text.startswith("\ufeff")
            assert SECRET not in as_csv.text
            reader = csv.reader(io.StringIO(as_csv.text.lstrip("\ufeff")))
            rows = list(reader)
            assert rows[0][0] == "case_name"
            assert len(rows) == 2
            assert rows[1][1] in ("passed", "failed", "error")

            missing = await client.get(
                f"/api/v1/evals/runs/{'e' * 32}/export",
                headers=headers,
                params={"format": "json"},
            )
            assert missing.status_code == 404, missing.text
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)
