"""画布运行状态：最近一次运行的节点状态接口，以及停止运行（真实后端 + 真实 PG）。

画布进入时靠 /workflows/{id}/runs/latest 恢复每个节点上次跑成什么样；
停在人工节点时 gate / gate_kind 要一起回来，节点上才能显示「待审批 / 待填写」。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _definition() -> dict:
    """start → approval → end：跑一次就会停在人工节点。"""

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
            {"id": "gate", "kind": "approval", "label": "值班确认", "prompt": "确认处置 {{topic}}"},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate", "route": ""},
            {"id": "e2", "source": "gate", "target": "end", "route": ""},
        ],
    }


@pytest.mark.asyncio
async def test_latest_run_snapshot_and_stop(env: dict[str, str], api_base: str) -> None:
    prefix = f"it-state-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={"name": prefix, "description": "", "definition": _definition()},
        )
        assert created.status_code == 200, created.text
        workflow_id = created.json()["id"]
        try:
            # 还没跑过：没有可恢复的状态
            empty = await client.get(
                f"/api/v1/workflows/{workflow_id}/runs/latest", headers=headers
            )
            assert empty.status_code == 200, empty.text
            assert empty.json()["run"] is None

            run = await client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                headers=headers,
                json={"values": {"topic": "磁盘告警"}},
            )
            assert run.status_code == 200, run.text
            run_uid = run.json()["run_uid"]
            assert run.json()["status"] == "waiting"

            snapshot = await client.get(
                f"/api/v1/workflows/{workflow_id}/runs/latest", headers=headers
            )
            assert snapshot.status_code == 200, snapshot.text
            body = snapshot.json()["run"]
            assert body["run_uid"] == run_uid
            assert body["status"] == "waiting"
            # 停在人工节点：节点上要显示待审批，而不是「成功」
            assert body["gate"] == "gate"
            assert body["gate_kind"] == "approval"
            executed = {item["node_id"]: item["status"] for item in body["nodes"]}
            assert executed["start"] == "succeeded"
            assert "end" not in executed

            # 已经不动的运行没有可停止的回合，如实回 stopped=false
            stop = await client.post(
                f"/api/v1/workflows/runs/{run_uid}/stop", headers=headers
            )
            assert stop.status_code == 200, stop.text
            assert stop.json() == {"stopped": False}

            missing = await client.post(
                "/api/v1/workflows/runs/run_not_exists/stop", headers=headers
            )
            assert missing.status_code == 404, missing.text
        finally:
            await client.delete(f"/api/v1/workflows/{workflow_id}", headers=headers)


@pytest.mark.asyncio
async def test_latest_run_snapshot_404_for_unknown_workflow(
    env: dict[str, str], api_base: str
) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=30.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        response = await client.get(
            "/api/v1/workflows/99999999/runs/latest",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404, response.text


def test_run_control_stop_signal() -> None:
    """停止标记只对进行中的运行生效，结束/未开始的运行必须回 False。"""

    from app.services import run_control

    run_control.begin_run("run_stop_case")
    try:
        assert run_control.is_stopped("run_stop_case") is False
        assert run_control.request_stop("run_stop_case") is True
        assert run_control.is_stopped("run_stop_case") is True
    finally:
        run_control.end_run("run_stop_case")

    # 已经结束：标记清干净，同一 uid 再跑不会被上一次的停止请求误伤
    assert run_control.is_stopped("run_stop_case") is False
    assert run_control.request_stop("run_stop_case") is False

    # 重新开始会清掉遗留标记
    run_control.begin_run("run_stop_case")
    assert run_control.is_stopped("run_stop_case") is False
    run_control.end_run("run_stop_case")
