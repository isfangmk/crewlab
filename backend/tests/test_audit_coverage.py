"""真实 PG：审计覆盖面 —— 配置变更、知识文档、分享与导出。

对话内工具调用由 observation_tool_calls 记录 trace，不进审计表；
审计表只收「谁改了什么」这类可追责的动作。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _pick_model(catalog: dict) -> tuple[str, str]:
    providers = catalog.get("providers")
    if not isinstance(providers, list) or len(providers) == 0:
        raise LookupError("模型目录缺少 providers")
    p0 = providers[0]
    provider = p0.get("id")
    models = p0.get("models")
    if not isinstance(provider, str) or provider == "":
        raise LookupError("provider.id 无效")
    if not isinstance(models, list) or len(models) == 0:
        raise LookupError("provider.models 为空")
    model = models[0]
    if not isinstance(model, str) or model == "":
        raise LookupError("model 无效")
    return provider, model


async def _audit_rows(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    action: str,
    resource_id: str,
) -> list[dict]:
    res = await client.get(
        "/api/v1/audit",
        headers=headers,
        params={"action": action, "resource_id": resource_id, "limit": 50},
    )
    assert res.status_code == 200, res.text
    return res.json()["items"]


@pytest.mark.asyncio
async def test_agent_crud_is_audited(env: dict[str, str], api_base: str) -> None:
    """助理的装配变更（人设/工具白名单/模型）必须留痕。"""

    name = f"audit-agent-{uuid.uuid4().hex[:6]}"
    payload = {
        "name": name,
        "description": "审计用助理",
        "when_to_use": "仅测试",
        "system_prompt": "你是审计测试助理。",
        "critical_reminder": "",
        "allowed_tools": ["get_current_time"],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 8,
        "output_format": "checklist",
        "examples": [{"input": "巡检怎么开始？", "output": "- 先看黄金四信号"}],
        "knowledge_scope": {"paths": [], "doc_ids": []},
        # 不装配技能：技能的声明工具会自动并入白名单，会让本用例的工具断言随目录漂移
        "skills": [],
    }
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post("/api/v1/agents/custom", headers=headers, json=payload)
        assert created.status_code == 200, created.text
        try:
            updated = await client.patch(
                f"/api/v1/agents/custom/{name}",
                headers=headers,
                json={**payload, "max_turns": 12, "allowed_tools": []},
            )
            assert updated.status_code == 200, updated.text
        finally:
            deleted = await client.delete(f"/api/v1/agents/custom/{name}", headers=headers)
            assert deleted.status_code == 200, deleted.text

        rows = await _audit_rows(client, headers, action="agent.create", resource_id=name)
        assert len(rows) == 1, rows
        assert rows[0]["resource_type"] == "agent"
        assert rows[0]["detail"]["max_turns"] == 8
        assert rows[0]["detail"]["tools"] == ["get_current_time"]
        # 审计只记 prompt 长度，不落全文；示例只记条数
        assert rows[0]["detail"]["system_prompt_chars"] == len(payload["system_prompt"])
        assert rows[0]["detail"]["output_format"] == "checklist"
        assert rows[0]["detail"]["examples"] == 1

        upd = await _audit_rows(client, headers, action="agent.update", resource_id=name)
        assert len(upd) == 1, upd
        assert upd[0]["detail"]["max_turns"] == 12
        assert upd[0]["detail"]["tools"] == []

        dele = await _audit_rows(client, headers, action="agent.delete", resource_id=name)
        assert len(dele) == 1, dele


@pytest.mark.asyncio
async def test_knowledge_doc_crud_is_audited(env: dict[str, str], api_base: str) -> None:
    """知识文档的新增/修改/移动/删除逐条留痕，资源 id 指回同一篇文档。"""

    title = f"audit-doc-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/knowledge/docs",
            headers=headers,
            json={"title": title, "content": "审计覆盖测试文档内容。", "path": "审计/原始"},
        )
        if created.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {created.text}")
        assert created.status_code == 200, created.text
        doc_id = created.json()["id"]

        try:
            updated = await client.patch(
                f"/api/v1/knowledge/docs/{doc_id}",
                headers=headers,
                json={"title": title, "content": "审计覆盖测试文档内容（已更新）。"},
            )
            assert updated.status_code == 200, updated.text
            moved = await client.patch(
                f"/api/v1/knowledge/docs/{doc_id}/move",
                headers=headers,
                json={"path": "审计/已归档"},
            )
            assert moved.status_code == 200, moved.text
        finally:
            deleted = await client.delete(f"/api/v1/knowledge/docs/{doc_id}", headers=headers)
            assert deleted.status_code == 200, deleted.text

        create_rows = await _audit_rows(client, headers, action="knowledge.doc_create", resource_id=doc_id)
        assert len(create_rows) == 1, create_rows
        assert create_rows[0]["detail"]["title"] == title

        update_rows = await _audit_rows(client, headers, action="knowledge.doc_update", resource_id=doc_id)
        assert len(update_rows) == 1, update_rows

        move_rows = await _audit_rows(client, headers, action="knowledge.doc_move", resource_id=doc_id)
        assert len(move_rows) == 1, move_rows
        assert move_rows[0]["detail"]["path"] == "审计/已归档"

        delete_rows = await _audit_rows(client, headers, action="knowledge.doc_delete", resource_id=doc_id)
        assert len(delete_rows) == 1, delete_rows
        # 删除后仍能凭 detail.title 辨认被删的是哪篇
        assert delete_rows[0]["detail"]["title"] == title


@pytest.mark.asyncio
async def test_setting_update_and_session_share_are_audited(
    env: dict[str, str], api_base: str
) -> None:
    """系统设置变更与对外分享链接都属于需要追责的动作。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        original = await client.get(
            "/api/v1/system-settings", headers=headers, params={"category": "agent"}
        )
        assert original.status_code == 200, original.text
        current = {i["key"]: i["value"] for i in original.json()["items"]}
        assert "output_locale" in current, current
        before = current["output_locale"]

        marker = "en" if before != "en" else "zh"
        try:
            put = await client.put(
                "/api/v1/system-settings/agent/output_locale",
                headers=headers,
                json={"value": marker},
            )
            assert put.status_code == 200, put.text
            rows = await _audit_rows(
                client, headers, action="setting.update", resource_id="agent.output_locale"
            )
            assert len(rows) >= 1, rows
            assert rows[0]["detail"]["value"] == marker
        finally:
            restore = await client.put(
                "/api/v1/system-settings/agent/output_locale",
                headers=headers,
                json={"value": before},
            )
            assert restore.status_code == 200, restore.text

        catalog = await client.get("/api/v1/aiops/models", headers=headers)
        if catalog.status_code != 200:
            pytest.skip("无可用模型")
        try:
            provider, model = _pick_model(catalog.json())
        except LookupError as exc:
            pytest.skip(str(exc))

        session = await client.post(
            "/api/v1/chat/sessions",
            headers=headers,
            json={
                "title": f"审计分享-{uuid.uuid4().hex[:6]}",
                "provider": provider,
                "model": model,
                "agent_id": "default",
            },
        )
        assert session.status_code == 200, session.text
        session_id = session.json()["id"]
        share = await client.post(
            f"/api/v1/chat/sessions/{session_id}/share",
            headers=headers,
            json={"expires_in_hours": 1},
        )
        assert share.status_code == 200, share.text
        rows = await _audit_rows(client, headers, action="session.share", resource_id=session_id)
        assert len(rows) == 1, rows
        assert rows[0]["resource_type"] == "chat_session"
        assert rows[0]["detail"]["expires_in_hours"] == 1


@pytest.mark.asyncio
async def test_audit_export_csv(env: dict[str, str], api_base: str) -> None:
    """导出与列表同一套筛选，CSV 带表头与中文 BOM。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        email = f"csv_{uuid.uuid4().hex[:6]}@local"
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "CSV Target",
                "password": "csv-pass-123",
                "role": "viewer",
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        try:
            res = await client.get(
                "/api/v1/audit/export",
                headers=headers,
                params={"action": "user.create", "resource_id": str(user_id)},
            )
            assert res.status_code == 200, res.text
            assert res.headers["content-type"].startswith("text/csv")
            text = res.content.decode("utf-8")
            assert text.startswith("\ufeff"), "CSV 应带 BOM 以兼容 Excel"
            lines = [line for line in text.splitlines() if line.strip()]
            assert lines[0].lstrip("\ufeff").startswith("时间,操作者,动作")
            assert len(lines) == 2, lines
            assert "user.create" in lines[1]
            assert email in lines[1]
        finally:
            await client.delete(f"/api/v1/users/{user_id}", headers=headers)

        # 非管理员不可导出
        vemail = f"csvv_{uuid.uuid4().hex[:6]}@local"
        vcreate = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": vemail,
                "display_name": "CSV Viewer",
                "password": "view-pass-123",
                "role": "viewer",
            },
        )
        assert vcreate.status_code == 200, vcreate.text
        try:
            vlogin = await client.post(
                "/api/v1/auth/login",
                json={"email": vemail, "password": "view-pass-123"},
            )
            assert vlogin.status_code == 200, vlogin.text
            denied = await client.get(
                "/api/v1/audit/export",
                headers={"Authorization": f"Bearer {vlogin.json()['access_token']}"},
            )
            assert denied.status_code == 403, denied.text
        finally:
            await client.delete(f"/api/v1/users/{vcreate.json()['id']}", headers=headers)
