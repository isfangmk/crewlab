"""助理人设组装与示例校验。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.services.agent_service import (
    ResolvedAgent,
    compose_persona,
    parse_examples,
)
from app.services.knowledge_scope import KnowledgeScope
from tests.conftest import login_access_token


def _agent(**overrides: object) -> ResolvedAgent:
    base: dict = {
        "name": "test-agent",
        "description": "测试助理",
        "when_to_use": "",
        "system_prompt": "你是排障助理。",
        "critical_reminder": "",
        "tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 0,
        "source": "user",
    }
    base.update(overrides)
    return ResolvedAgent(**base)


def test_compose_persona_sections_ordered() -> None:
    """正文 → 必须遵守 → 输出格式 → 示例 → 知识范围，顺序是运行时契约。"""

    persona = compose_persona(
        _agent(
            critical_reminder="没有证据不得下结论。",
            output_format="checklist",
            examples=({"input": "怎么查？", "output": "- 先看指标"},),
            knowledge_scope=KnowledgeScope(paths=("网络",), doc_ids=()),
        )
    )
    idx_prompt = persona.index("你是排障助理。")
    idx_reminder = persona.index("## 必须遵守")
    idx_format = persona.index("## 输出格式")
    idx_examples = persona.index("## 示例")
    idx_scope = persona.index("## 知识范围")
    assert idx_prompt < idx_reminder < idx_format < idx_examples < idx_scope
    assert "没有证据不得下结论。" in persona
    assert "用户：怎么查？" in persona
    assert "助理：- 先看指标" in persona
    assert "网络" in persona


def test_compose_persona_free_format_has_no_section() -> None:
    persona = compose_persona(_agent(output_format="free"))
    assert persona == "你是排障助理。"


def test_compose_persona_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        compose_persona(_agent(output_format="yaml"))


def test_parse_examples_limits() -> None:
    ok = parse_examples([{"input": "a", "output": "b"}])
    assert ok == ({"input": "a", "output": "b"},)

    with pytest.raises(ValueError):
        parse_examples([{"input": "a", "output": "b"}] * 4)
    with pytest.raises(ValueError):
        parse_examples([{"input": "", "output": "b"}])
    with pytest.raises(ValueError):
        parse_examples([{"input": "a" * 2001, "output": "b"}])
    with pytest.raises(TypeError):
        parse_examples("not-a-list")


@pytest.mark.asyncio
async def test_agent_persona_round_trip(env: dict[str, str], api_base: str) -> None:
    """示例/输出格式/知识范围必须能原样读回，未知取值显式失败。"""

    name = f"persona-agent-{uuid.uuid4().hex[:6]}"
    payload = {
        "name": name,
        "description": "人设测试助理",
        "when_to_use": "仅测试",
        "system_prompt": "你是人设测试助理。",
        "critical_reminder": "禁止臆测。",
        "allowed_tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 0,
        "output_format": "json",
        "examples": [{"input": "输出一份清单", "output": "{}"}],
        "knowledge_scope": {"paths": [], "doc_ids": []},
    }
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post("/api/v1/agents/custom", headers=headers, json=payload)
        assert created.status_code == 200, created.text
        try:
            body = created.json()
            assert body["output_format"] == "json"
            assert body["examples"] == payload["examples"]
            assert body["knowledge_scope"] == {"paths": [], "doc_ids": []}
            assert body["critical_reminder"] == "禁止臆测。"

            bad_format = await client.patch(
                f"/api/v1/agents/custom/{name}",
                headers=headers,
                json={**payload, "output_format": "yaml"},
            )
            assert bad_format.status_code == 400, bad_format.text

            bad_examples = await client.patch(
                f"/api/v1/agents/custom/{name}",
                headers=headers,
                json={**payload, "examples": [{"input": "", "output": "x"}]},
            )
            assert bad_examples.status_code == 400, bad_examples.text

            updated = await client.patch(
                f"/api/v1/agents/custom/{name}",
                headers=headers,
                json={**payload, "output_format": "free", "examples": []},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["output_format"] == "free"
            assert updated.json()["examples"] == []
        finally:
            deleted = await client.delete(f"/api/v1/agents/custom/{name}", headers=headers)
            assert deleted.status_code == 200, deleted.text


@pytest.mark.asyncio
async def test_agent_scope_rejects_unknown_path(env: dict[str, str], api_base: str) -> None:
    """不存在的目录不能静默存进助理配置。"""

    name = f"scope-agent-{uuid.uuid4().hex[:6]}"
    missing = f"不存在目录-{uuid.uuid4().hex[:6]}"
    payload = {
        "name": name,
        "description": "范围校验助理",
        "when_to_use": "",
        "system_prompt": "你是范围校验助理。",
        "critical_reminder": "",
        "allowed_tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 0,
        "output_format": "free",
        "examples": [],
        "knowledge_scope": {"paths": [missing], "doc_ids": []},
    }
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post("/api/v1/agents/custom", headers=headers, json=payload)
        assert resp.status_code == 400, resp.text
        assert missing in resp.json()["detail"]
