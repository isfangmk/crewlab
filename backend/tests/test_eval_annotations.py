"""人工标注：认领 / 提交 / 覆盖 / 一致性统计全链路。

需要一条真实评测结果作为标注对象（一条短用例的运行）；第二个标注人
直接写库模拟多人标注，验证一致性统计口径，数据随评测集删除级联清理。
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


def test_validate_annotation_fields_rejects_bad_label() -> None:
    """标签只接受 good / needs_work / bad。"""

    from app.services import annotation_service

    with pytest.raises(ValueError):
        annotation_service.validate_annotation_fields(label="great", comment="")
    fields = annotation_service.validate_annotation_fields(
        label="good", comment="  不错  "
    )
    assert fields == {"label": "good", "comment": "不错"}


async def test_annotation_claim_submit_consistency(
    env: dict[str, str], api_base: str
) -> None:
    """认领推进、同人提交覆盖、多人标注一致性统计。"""

    tag = f"eval-ann-{uuid.uuid4().hex[:8]}"
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
                json={"name": tag, "description": "标注链路"},
            )
            assert created.status_code == 200, created.text
            dataset_id = created.json()["id"]
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
            if not detail["results"] or any(
                item["status"] == "error" for item in detail["results"]
            ):
                pytest.skip("评测模型不可达，跳过标注链路断言")
            result_id = detail["results"][0]["id"]

            # 队列首条 = 尚未标注的结果
            claim = await client.get(
                "/api/v1/evals/annotations/queue",
                headers=headers,
                params={"run_uid": run_uid},
            )
            assert claim.status_code == 200, claim.text
            assert claim.json()["result"]["id"] == result_id

            submit = await client.post(
                "/api/v1/evals/annotations",
                headers=headers,
                json={"result_id": result_id, "label": "good", "comment": "不错"},
            )
            assert submit.status_code == 200, submit.text
            annotation_id = submit.json()["id"]

            # 同一人再提交 = 覆盖而不是新增
            resubmit = await client.post(
                "/api/v1/evals/annotations",
                headers=headers,
                json={"result_id": result_id, "label": "bad", "comment": "改判"},
            )
            assert resubmit.status_code == 200, resubmit.text
            assert resubmit.json()["id"] == annotation_id
            assert resubmit.json()["label"] == "bad"

            listed = await client.get(
                f"/api/v1/evals/runs/{run_uid}/annotations", headers=headers
            )
            assert listed.status_code == 200, listed.text
            body = listed.json()
            assert body["total"] == 1
            assert body["consistency"]["annotated_results"] == 1
            assert body["consistency"]["multi_annotated"] == 0
            assert body["consistency"]["agreement_rate"] is None
            assert body["consistency"]["label_counts"]["bad"] == 1

            # 本人已标注 → 队列清空
            empty = await client.get(
                "/api/v1/evals/annotations/queue",
                headers=headers,
                params={"run_uid": run_uid},
            )
            assert empty.status_code == 200, empty.text
            assert empty.json()["result"] is None

            updated = await client.post(
                f"/api/v1/evals/annotations/{annotation_id}",
                headers=headers,
                json={"label": "good", "comment": "复核后改回"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["label"] == "good"

            bad_label = await client.post(
                "/api/v1/evals/annotations",
                headers=headers,
                json={"result_id": result_id, "label": "great", "comment": ""},
            )
            assert bad_label.status_code == 400, bad_label.text

            # 第二个标注人直接写库，验证多人一致性统计
            from app.db import SessionLocal
            from app.models.eval import EvalAnnotation

            async with SessionLocal() as db:
                other = EvalAnnotation(
                    id=uuid.uuid4().hex,
                    result_id=result_id,
                    annotator_user_id=999999,
                    label="good",
                    comment="同感",
                )
                other_id = other.id
                db.add(other)
                await db.commit()

            stats = await client.get(
                f"/api/v1/evals/runs/{run_uid}/annotations", headers=headers
            )
            assert stats.status_code == 200, stats.text
            consistency = stats.json()["consistency"]
            assert consistency["multi_annotated"] == 1
            assert consistency["agreement_rate"] == 1.0

            async with SessionLocal() as db:
                row = await db.get(EvalAnnotation, other_id)
                row.label = "needs_work"
                await db.commit()

            stats2 = await client.get(
                f"/api/v1/evals/runs/{run_uid}/annotations", headers=headers
            )
            assert stats2.json()["consistency"]["agreement_rate"] == 0.0
        finally:
            if dataset_id is not None:
                await client.delete(
                    f"/api/v1/evals/datasets/{dataset_id}", headers=headers
                )
            await _cleanup_sessions(session_ids)
