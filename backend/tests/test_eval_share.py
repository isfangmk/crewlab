"""评测运行只读分享：免登录载荷结构、脱敏、过期/坏 token 显式失败。

复用评测导出用例的模式：跑一条真实 echo 评测运行作为分享对象，
用例名埋 sk- 形态假密钥，断言公开载荷只含遮蔽值；数据随数据集删除级联清理。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

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


async def test_share_report_public_redacted(env: dict[str, str], api_base: str) -> None:
    """免登录读分享报告：结构完整、密钥遮蔽、坏/过期 token 404。"""

    tag = f"eval-share-{uuid.uuid4().hex[:8]}"
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
                json={"name": tag, "description": "分享报告"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]
            case = await client.post(
                f"/api/v1/evals/datasets/{dataset_id}/cases",
                headers=headers,
                json={
                    "name": f"分享-{SECRET}",
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
                pytest.skip("评测没有产生结果，跳过分享断言")

            # 创建分享：明文 token 只此一次返回
            shared = await client.post(
                f"/api/v1/evals/runs/{run_uid}/share",
                headers=headers,
                json={"expires_in_hours": 24},
            )
            assert shared.status_code == 200, shared.text
            share_body = shared.json()
            raw = share_body["token"]
            assert isinstance(raw, str) and raw != ""
            assert share_body["run_uid"] == run_uid

            # 免登录读取：不带任何 Authorization 头
            public = await client.get(f"/api/v1/evals/shares/{raw}")
            assert public.status_code == 200, public.text
            payload = public.json()
            for key in (
                "expires_at",
                "dataset",
                "run",
                "cases_snapshot",
                "results",
                "gate",
                "consistency",
            ):
                assert key in payload, key
            assert payload["run"]["run_uid"] == run_uid
            assert payload["dataset"]["name"] == tag
            assert payload["results"][0]["case_name"].startswith("分享-sk-abcdefgh")
            assert isinstance(payload["results"][0]["judge_scores"], list)
            assert payload["consistency"]["results_total"] >= 1
            # 没配基线：gate 为 null 而不是报错
            assert payload["gate"] is None
            # 脱敏：假密钥只出现遮蔽形态
            assert SECRET not in public.text
            assert "sk-abcdefgh***" in public.text

            # 坏 token 显式 404
            missing = await client.get(f"/api/v1/evals/shares/{'e' * 32}")
            assert missing.status_code == 404, missing.text

            # 过期 token 显式 404：直接插一条已过期的分享行
            from app.db import SessionLocal
            from app.models.eval_share import EvalShareToken
            from app.services.platform.auth_service import hash_token

            expired_raw = f"expired-{uuid.uuid4().hex}"
            async with SessionLocal() as db:
                db.add(
                    EvalShareToken(
                        run_uid=run_uid,
                        token_hash=hash_token(expired_raw),
                        created_by=None,
                        expires_at=datetime.now(timezone.utc)
                        - timedelta(hours=1),
                    )
                )
                await db.commit()
            expired = await client.get(f"/api/v1/evals/shares/{expired_raw}")
            assert expired.status_code == 404, expired.text

            # 分享不存在的运行：404
            bad_run = await client.post(
                f"/api/v1/evals/runs/{'e' * 32}/share",
                headers=headers,
                json={"expires_in_hours": 24},
            )
            assert bad_run.status_code == 404, bad_run.text

            # 过期时长越界：422
            invalid = await client.post(
                f"/api/v1/evals/runs/{run_uid}/share",
                headers=headers,
                json={"expires_in_hours": 0},
            )
            assert invalid.status_code == 422, invalid.text
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)
