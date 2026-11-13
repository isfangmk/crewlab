"""自动化触发器：cron 计算、CRUD 集成、webhook fire 真实工作流运行。"""

from __future__ import annotations

import json

import httpx
import pytest
import uuid

from app.services.trigger_service import (
    TriggerValidationError,
    compute_next_fire,
    validate_rule,
)


def test_compute_next_fire_advances() -> None:
    """next_fire 严格晚于 base；每分钟表达式两次计算结果不同。"""

    from datetime import datetime, timezone

    base = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)
    n1 = compute_next_fire("* * * * *", base)
    assert n1 > base
    n2 = compute_next_fire("* * * * *", n1)
    assert n2 > n1


def test_validate_rule_rejects_bad_cron_and_missing_target() -> None:
    """非法 cron / 缺目标 / 空 prompt 显式失败。"""

    with pytest.raises(TriggerValidationError, match="cron 表达式非法"):
        validate_rule(
            kind="cron",
            cron_expr="not a cron",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="workflow_id"):
        validate_rule(
            kind="cron",
            cron_expr="* * * * *",
            target_kind="workflow",
            workflow_id=None,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="prompt"):
        validate_rule(
            kind="cron",
            cron_expr="* * * * *",
            target_kind="agent",
            workflow_id=None,
            agent_id="default",
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="类型非法"):
        validate_rule(
            kind="hourly",
            cron_expr="* * * * *",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )


async def test_trigger_crud_and_webhook_fire(
    monkeypatch, env: dict[str, str], api_base: str
) -> None:
    """真后端集成：创建 webhook 触发器 → 公开端点 fire → 真实工作流运行产生。"""

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        # 前置：建一个可发布的演示工作流
        definition = {
            "nodes": [
                {
                    "id": "start",
                    "kind": "start",
                    "label": "开始",
                    "variables": [
                        {"name": "topic", "type": "string", "description": "", "required": True, "default": None}
                    ],
                },
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"id": "e1", "source": "start", "target": "end", "route": ""}],
        }
        wf = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={"name": "trigger-demo-" + uuid.uuid4().hex[:8], "description": "触发器联调", "definition": definition},
        )
        assert wf.status_code == 200, wf.text
        workflow_id = wf.json()["id"]

        # 创建 webhook 触发器（目标=该工作流）
        created = await client.post(
            "/api/v1/triggers",
            headers=headers,
            json={
                "name": "webhook-demo",
                "kind": "webhook",
                "target_kind": "workflow",
                "workflow_id": workflow_id,
                "values_json": json.dumps({"topic": "自动巡检"}),
            },
        )
        assert created.status_code == 200, created.text
        rule = created.json()
        assert rule["has_webhook"] is True
        webhook_url = created.json()["webhook_url"]
        assert webhook_url.startswith("/api/v1/triggers/webhook/")

        # 触发器运行已发布版本：先发布演示工作流
        pub = await client.post(f"{api_base}/api/v1/workflows/{workflow_id}/publish", headers=headers)
        assert pub.status_code == 200, pub.text

        try:
            # 错 token 显式 404
            bad = await client.post("/api/v1/triggers/webhook/not-the-token/fire")
            assert bad.status_code == 404

            # 公开 fire（不带登录头）：真实触发一次工作流运行
            fired = await client.post(f"{api_base}{webhook_url}")
            assert fired.status_code == 200, fired.text
            run_uid = fired.json()["ref_id"]
            assert run_uid.startswith("run_")
        finally:
            await client.delete(f"/api/v1/workflows/{workflow_id}", headers=headers)

        # 触发记录随工作流删除级联清理
        runs = await client.get(
            f"/api/v1/triggers/{rule['id']}/runs", headers=headers
        )
        assert runs.status_code == 200
        assert runs.json()["total"] == 0


async def test_agent_trigger_session_created(
    monkeypatch, env: dict[str, str], api_base: str
) -> None:
    """agent 型触发器 fire 后真实发起一轮助理会话（消耗 1 轮对话）。"""

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=180.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/triggers",
            headers=headers,
            json={
                "name": "agent-trigger-demo",
                "kind": "webhook",
                "target_kind": "agent",
                "agent_id": "default",
                "prompt": "用一句话回答：7 乘 6 等于几？",
            },
        )
        assert created.status_code == 200, created.text
        rule = created.json()
        webhook_url = rule["webhook_url"]

        fired = await client.post(f"{api_base}{webhook_url}")
        assert fired.status_code == 200, fired.text
        run = fired.json()
        assert run["status"] == "succeeded", run["error"]
        assert run["ref_id"].startswith("sess_") or len(run["ref_id"]) == 36

        # 触发记录：waiting（审批）或 succeeded
        runs = await client.get(f"/api/v1/triggers/{rule['id']}/runs", headers=headers)
        assert runs.status_code == 200
        assert runs.json()["total"] >= 1
