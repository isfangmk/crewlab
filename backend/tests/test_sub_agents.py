"""子 Agent 委托：保存校验（自引用/不存在/防环）+ 真实委托回合（消耗一轮对话）。"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _agent_body(
    *, name: str, description: str, sub_agents: list[str] | None = None
) -> dict:
    """创建/更新共用的完整 schema 形状 body。"""

    return {
        "name": name,
        "description": description,
        "when_to_use": f"{description}（委托用）",
        "system_prompt": f"你是 {name}，只回答与你职责相关的问题。",
        "critical_reminder": "",
        "allowed_tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 12,
        "output_format": "free",
        "examples": [],
        "knowledge_scope": {"paths": [], "doc_ids": []},
        "skills": None,
        "sub_agents": sub_agents or [],
    }


async def _create_agent(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    name: str,
    description: str,
    sub_agents: list[str] | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/agents/custom",
        headers=headers,
        json=_agent_body(name=name, description=description, sub_agents=sub_agents),
    )


async def test_sub_agent_validation_rejects_self_and_missing(env, api_base) -> None:
    """创建时自引用 / 引用不存在的助理显式 400。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        name = f"self-ref-{uuid.uuid4().hex[:8]}"

        r = await _create_agent(
            client, headers, name=name, description="x", sub_agents=[name]
        )
        assert r.status_code == 400
        assert "委托给自己" in r.text

        r = await _create_agent(
            client,
            headers,
            name=name,
            description="x",
            sub_agents=["no-such-agent-x"],
        )
        assert r.status_code == 400
        assert "子助理不存在" in r.text


async def test_sub_agent_cycle_rejected_on_update(env, api_base) -> None:
    """子已委托父时，把父改为委托子显式 400（成环）。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        parent = f"cycle-parent-{uuid.uuid4().hex[:8]}"
        child = f"cycle-child-{uuid.uuid4().hex[:8]}"

        r = await _create_agent(client, headers, name=parent, description="父")
        assert r.status_code == 200, r.text
        r = await _create_agent(
            client,
            headers,
            name=child,
            description="子",
            sub_agents=[parent],
        )
        assert r.status_code == 200, r.text
        # 父改为委托子：父 → 子 → 父，成环 → 400
        r = await client.patch(
            f"/api/v1/agents/custom/{parent}",
            headers=headers,
            json=_agent_body(name=parent, description="父", sub_agents=[child]),
        )
        assert r.status_code == 400, r.text
        assert "成环" in r.text

        await client.delete(f"/api/v1/agents/custom/{child}", headers=headers)
        await client.delete(f"/api/v1/agents/custom/{parent}", headers=headers)


async def test_referenced_sub_agent_cannot_be_deleted(env, api_base) -> None:
    """仍被父助理委托的子助理不能删除（显式 400），否则父助理运行期会断链。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        parent = f"ref-parent-{uuid.uuid4().hex[:8]}"
        child = f"ref-child-{uuid.uuid4().hex[:8]}"

        await _create_agent(client, headers, name=child, description="子")
        r = await _create_agent(
            client, headers, name=parent, description="父", sub_agents=[child]
        )
        assert r.status_code == 200, r.text
        try:
            r = await client.delete(f"/api/v1/agents/custom/{child}", headers=headers)
            assert r.status_code == 400, r.text
            assert "委托" in r.text
        finally:
            await client.delete(f"/api/v1/agents/custom/{parent}", headers=headers)
            await client.delete(f"/api/v1/agents/custom/{child}", headers=headers)


async def test_delegation_round(env, api_base) -> None:
    """父助理把计算委托给子助理并返回结果（真实委托回合，消耗 1 轮对话）。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=240.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        suffix = uuid.uuid4().hex[:8]
        parent = f"dele-parent-{suffix}"
        child = f"dele-child-{suffix}"

        await _create_agent(
            client, headers, name=child, description="数学计算专员：只负责算数"
        )
        parent_resp = await _create_agent(
            client,
            headers,
            name=parent,
            description="前台助理：计算类问题委托给子助理",
            sub_agents=[child],
        )
        assert parent_resp.status_code == 200, parent_resp.text

        try:
            session = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": "delegation-e2e",
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "agent_id": parent,
                },
            )
            assert session.status_code == 200, session.text
            session_id = session.json()["id"]

            answer = ""
            async with client.stream(
                "POST",
                f"/api/v1/chat/sessions/{session_id}/messages/stream",
                headers=headers,
                json={
                    "content": "请委托数学专员计算 41+1，并告诉我结果。",
                    "thinking_enabled": False,
                    "web_search_enabled": False,
                    "attachment_ids": [],
                },
            ) as resp:
                assert resp.status_code == 200, resp.status_code
                buf = ""
                event = ""
                async for chunk in resp.aiter_text():
                    lines = (buf + chunk).split("\n")
                    buf = lines[-1]
                    for line in lines[:-1]:
                        if line.startswith("event: "):
                            event = line[7:].strip()
                        elif line.startswith("data: ") and event == "assistant_delta":
                            try:
                                d = json.loads(line[6:])
                                answer += d.get("delta", "")
                            except json.JSONDecodeError:
                                pass
            # 会话消息里的最终回答复核
            msgs = await client.get(
                f"/api/v1/chat/sessions/{session_id}/messages", headers=headers
            )
            assert msgs.status_code == 200, msgs.text
            for m in msgs.json()["items"]:
                if m.get("kind") == "message" and m.get("role") == "assistant":
                    answer = str(m.get("content") or answer)
            assert "42" in answer, f"回答未包含 42: {answer[:200]}"
        finally:
            for name in (parent, child):
                await client.delete(f"/api/v1/agents/custom/{name}", headers=headers)
