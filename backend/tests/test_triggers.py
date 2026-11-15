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


def test_compute_next_fire_uses_system_timezone() -> None:
    """0 9 * * * 表示系统时区的 9 点，而不是 UTC 9 点。"""

    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 11, 14, 0, 0, 0, tzinfo=timezone.utc)
    shanghai = timezone(timedelta(hours=8))
    nxt = compute_next_fire("0 9 * * *", base, shanghai)
    assert nxt == datetime(2026, 11, 14, 1, 0, 0, tzinfo=timezone.utc)


def test_scheduler_uses_service_compute_next_fire_and_exponential_backoff() -> None:
    """调度器必须能解析到 compute_next_fire；重试按指数退避。"""

    from app.services import trigger_scheduler

    assert trigger_scheduler.compute_next_fire is compute_next_fire
    assert trigger_scheduler._retry_delay_seconds(1) == 60
    assert trigger_scheduler._retry_delay_seconds(2) == 120
    assert trigger_scheduler._retry_delay_seconds(3) == 240


def test_validate_rule_rejects_bad_cron_and_missing_target() -> None:
    """非法 cron / 缺目标 / 空 prompt 显式失败。"""

    with pytest.raises(TriggerValidationError, match="cron 表达式非法"):
        validate_rule(
            name="bad-cron",
            kind="cron",
            cron_expr="61 * * * *",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="workflow_id"):
        validate_rule(
            name="no-target",
            kind="cron",
            cron_expr="* * * * *",
            target_kind="workflow",
            workflow_id=None,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="prompt"):
        validate_rule(
            name="no-prompt",
            kind="cron",
            cron_expr="* * * * *",
            target_kind="agent",
            workflow_id=None,
            agent_id="default",
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="类型非法"):
        validate_rule(
            name="bad-kind",
            kind="hourly",
            cron_expr="* * * * *",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="5 段"):
        validate_rule(
            name="six-fields",
            kind="cron",
            cron_expr="* * * * * *",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )
    with pytest.raises(TriggerValidationError, match="values_json"):
        validate_rule(
            name="bad-values",
            kind="webhook",
            cron_expr="",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
            values_json="{oops",
        )
    with pytest.raises(TriggerValidationError, match="名称"):
        validate_rule(
            name="   ",
            kind="webhook",
            cron_expr="",
            target_kind="workflow",
            workflow_id=1,
            agent_id=None,
            prompt="",
        )


async def test_trigger_crud_and_webhook_fire(
    env: dict[str, str], api_base: str
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
        rule_id: int | None = None

        try:
            # 触发器固定跑已发布快照：先发布，再创建
            pub = await client.post(
                f"{api_base}/api/v1/workflows/{workflow_id}/publish", headers=headers
            )
            assert pub.status_code == 200, pub.text

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
            rule_id = rule["id"]
            assert rule["has_webhook"] is True
            webhook_url = rule["webhook_url"]
            assert webhook_url.startswith("/api/v1/triggers/webhook/")

            # 错 token 显式 404
            bad = await client.post("/api/v1/triggers/webhook/not-the-token/fire")
            assert bad.status_code == 404

            # 公开 fire（不带登录头）：真实触发一次工作流运行
            fired = await client.post(f"{api_base}{webhook_url}")
            assert fired.status_code == 200, fired.text
            run_uid = fired.json()["ref_id"]
            assert run_uid.startswith("run_")

            runs = await client.get(
                f"/api/v1/triggers/{rule_id}/runs", headers=headers
            )
            assert runs.status_code == 200, runs.text
            assert runs.json()["total"] >= 1
        finally:
            deleted = await client.delete(
                f"/api/v1/workflows/{workflow_id}", headers=headers
            )
            assert deleted.status_code == 200, deleted.text

        # 触发器随工作流级联删除，不能再按 id 查到
        assert rule_id is not None
        gone = await client.get(f"/api/v1/triggers/{rule_id}", headers=headers)
        assert gone.status_code == 404, gone.text


async def test_agent_trigger_session_created(
    env: dict[str, str], api_base: str
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
        session_id = ""

        try:
            fired = await client.post(f"{api_base}{webhook_url}")
            assert fired.status_code == 200, fired.text
            run = fired.json()
            assert run["status"] == "succeeded", run["error"]
            assert run["ref_id"].startswith("sess_") or len(run["ref_id"]) == 36
            session_id = run["ref_id"]

            # 触发记录：waiting（审批）或 succeeded
            runs = await client.get(
                f"/api/v1/triggers/{rule['id']}/runs", headers=headers
            )
            assert runs.status_code == 200
            assert runs.json()["total"] >= 1
        finally:
            # 测试产生的规则与会话都要清掉，不能留在用户的自动化/会话列表里
            deleted_trigger = await client.delete(
                f"/api/v1/triggers/{rule['id']}", headers=headers
            )
            assert deleted_trigger.status_code == 200, deleted_trigger.text
            if session_id:
                deleted_session = await client.delete(
                    f"/api/v1/chat/sessions/{session_id}", headers=headers
                )
                assert deleted_session.status_code == 200, deleted_session.text


async def test_cron_next_fire_is_timezone_aware(
    env: dict[str, str], api_base: str
) -> None:
    """SQLite 读回的 next_fire_at 必须补上 UTC 时区，否则调度器与 now 比较会抛错。"""

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.trigger import TriggerRule
    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/triggers",
            headers=headers,
            json={
                "name": "cron-tz-" + uuid.uuid4().hex[:8],
                "kind": "cron",
                "cron_expr": "0 9 * * *",
                "target_kind": "agent",
                "agent_id": "default",
                "prompt": "巡检",
            },
        )
        assert created.status_code == 200, created.text
        rule_id = created.json()["id"]

        try:
            async with SessionLocal() as db:
                row = await db.scalar(
                    select(TriggerRule).where(TriggerRule.id == rule_id)
                )
                assert row is not None
                assert row.next_fire_at is not None
                assert row.next_fire_at.tzinfo is not None
        finally:
            deleted = await client.delete(
                f"/api/v1/triggers/{rule_id}", headers=headers
            )
            assert deleted.status_code == 200, deleted.text


def _definition_with_variables(names: list[str]) -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "label": "开始",
                "variables": [
                    {
                        "name": name,
                        "type": "string",
                        "description": "",
                        "required": True,
                        "default": None,
                    }
                    for name in names
                ],
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end", "route": ""}],
    }


async def test_workflow_detail_can_read_published_definition(
    env: dict[str, str], api_base: str
) -> None:
    """触发器按已发布快照声明变量：草稿后续新增变量不应改变外部触发的输入契约。"""

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        workflow_name = "source-published-" + uuid.uuid4().hex[:8]
        created = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "name": workflow_name,
                "description": "",
                "definition": _definition_with_variables(["topic"]),
            },
        )
        assert created.status_code == 200, created.text
        workflow_id = created.json()["id"]
        draft_only = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "name": "source-draft-only-" + uuid.uuid4().hex[:8],
                "description": "",
                "definition": _definition_with_variables(["topic"]),
            },
        )
        assert draft_only.status_code == 200, draft_only.text
        draft_id = draft_only.json()["id"]

        try:
            pub = await client.post(
                f"/api/v1/workflows/{workflow_id}/publish", headers=headers
            )
            assert pub.status_code == 200, pub.text
            # 发布后再改草稿，模拟「编辑器加了变量但还没重新发布」
            updated = await client.put(
                f"/api/v1/workflows/{workflow_id}",
                headers=headers,
                json={
                    "name": workflow_name,
                    "description": "",
                    "definition": _definition_with_variables(["topic", "urgent"]),
                },
            )
            assert updated.status_code == 200, updated.text

            published = await client.get(
                f"/api/v1/workflows/{workflow_id}?source=published", headers=headers
            )
            assert published.status_code == 200, published.text
            body = published.json()
            assert body["definition_source"] == "published"
            start = next(
                node for node in body["definition"]["nodes"] if node["kind"] == "start"
            )
            assert [v["name"] for v in start["variables"]] == ["topic"]

            draft = await client.get(f"/api/v1/workflows/{workflow_id}", headers=headers)
            assert draft.status_code == 200, draft.text
            assert draft.json()["definition_source"] == "draft"
            draft_start = next(
                node for node in draft.json()["definition"]["nodes"] if node["kind"] == "start"
            )
            assert [v["name"] for v in draft_start["variables"]] == ["topic", "urgent"]

            # 从未发布的工作流没有快照可读，显式 400 而不是静默退回草稿
            missing = await client.get(
                f"/api/v1/workflows/{draft_id}?source=published", headers=headers
            )
            assert missing.status_code == 400, missing.text
        finally:
            for workflow_id_to_delete in (workflow_id, draft_id):
                deleted = await client.delete(
                    f"/api/v1/workflows/{workflow_id_to_delete}", headers=headers
                )
                assert deleted.status_code == 200, deleted.text
