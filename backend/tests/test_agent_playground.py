"""助理 Playground 试跑：流式帧与「不落会话」契约。"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.conftest import login_access_token


def _payload(name: str) -> dict:
    return {
        "name": name,
        "display_name": "试跑测试助理",
        "description": "试跑测试助理",
        "when_to_use": "仅测试",
        "system_prompt": "你是试跑测试助理，用一句话回答。",
        "critical_reminder": "",
        "allowed_tools": [],
        "disallowed_tools": [],
        "permission_mode": "",
        "model": "",
        "max_turns": 0,
        "output_format": "free",
        "examples": [],
        "knowledge_scope": {"paths": [], "doc_ids": []},
    }


@pytest.mark.asyncio
async def test_playground_streams_and_persists_nothing(env: dict[str, str], api_base: str) -> None:
    """试跑产出 SSE 帧，但不新建会话、不写消息。"""

    name = f"pg-agent-{uuid.uuid4().hex[:6]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post("/api/v1/agents/custom", headers=headers, json=_payload(name))
        assert created.status_code == 200, created.text
        try:
            before = await client.get("/api/v1/chat/sessions", headers=headers, params={"limit": 1})
            before_total = before.json()["total"]

            events: list[tuple[str, dict]] = []
            async with client.stream(
                "POST",
                "/api/v1/agents/playground/stream",
                headers=headers,
                json={
                    "agent": _payload(name),
                    "content": "说一句话",
                    "provider": "",
                    "model": "",
                    "thinking_enabled": False,
                    "web_search_enabled": False,
                    "locale": None,
                },
            ) as resp:
                assert resp.status_code == 200, resp.text
                event = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[len("data:"):].strip())
                        events.append((event, data))

            after = await client.get("/api/v1/chat/sessions", headers=headers, params={"limit": 1})
            assert after.json()["total"] == before_total, "试跑不得新建会话"

            names = [e[0] for e in events]
            # 模型不可用时会回 error 帧；只要不是静默空流即可
            assert names, "试跑必须产出 SSE 帧"
            assert any(n in ("assistant_delta", "done", "error") for n in names), names
            # error 帧必须带 error 字段，禁止静默吞掉
            for evt, data in events:
                if evt == "error":
                    assert isinstance(data.get("error"), str) and data["error"] != ""
        finally:
            deleted = await client.delete(f"/api/v1/agents/custom/{name}", headers=headers)
            assert deleted.status_code == 200, deleted.text
