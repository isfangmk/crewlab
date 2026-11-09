"""真实 custom 模型：开/关思考时 SSE 与上游 thinking 字段是否对齐。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (
    "逐步推理：水池 A 管 6 小时装满，B 管 8 小时装满，出水管 12 小时放空。"
    "三管同开多少小时装满？只给最终小时数。不要调用工具。"
)


def _load_env() -> dict[str, str]:
    path = ROOT / ".env"
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


async def _one_turn(
    client: httpx.AsyncClient, *, thinking_enabled: bool
) -> dict[str, object]:
    cr = await client.post(
        "/api/v1/chat/sessions",
        json={
            "title": f"thinking-test-{thinking_enabled}",
            "provider": "custom",
            "model": "workbuddy/glm-5.3-flash",
            "agent_id": "default",
        },
    )
    cr.raise_for_status()
    session_id = cr.json()["id"]

    thinking_n = 0
    assistant_n = 0
    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/messages/stream",
        json={
            "content": PROMPT,
            "thinking_enabled": thinking_enabled,
            "web_search_enabled": False,
            "mentions": [],
            "attachment_ids": [],
        },
    ) as r:
        r.raise_for_status()
        event_name = ""
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "":
                continue
            data = json.loads(raw)
            if event_name == "thinking_delta":
                thinking_n += 1
            elif event_name == "assistant_delta":
                assistant_n += 1
            elif event_name == "error":
                raise RuntimeError(data)

    return {
        "session_id": session_id,
        "thinking_n": thinking_n,
        "assistant_n": assistant_n,
    }


async def _upstream_reasoning_chunks(*, thinking_type: str) -> int:
    """直连 custom 上游，确认 thinking 字段能逼出 reasoning_content。"""

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models_orm.llm import LlmProvider

    async with SessionLocal() as db:
        p = await db.scalar(
            select(LlmProvider).where(LlmProvider.provider_id == "custom")
        )
        if p is None:
            raise RuntimeError("custom provider 不存在")
        base_url, api_key, model = p.base_url, p.api_key, p.default_model

    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 1024,
        "thinking": {"type": thinking_type},
    }
    reason_n = 0
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                delta = ((data.get("choices") or [{}])[0].get("delta")) or {}
                rc = delta.get("reasoning_content")
                if isinstance(rc, str) and rc != "":
                    reason_n += 1
    return reason_n


@pytest.mark.asyncio
async def test_custom_thinking_toggle_controls_thinking_delta() -> None:
    env = _load_env()
    token = env.get("AUTH_TOKEN")
    if not token:
        pytest.skip("缺少 AUTH_TOKEN")
    host = env.get("HOST", "127.0.0.1")
    port = env.get("PORT", "18080")
    base = f"http://{host}:{port}"

    # 上游 thinking=enabled 应能吐 reasoning（证明字段有效）；disabled 偶发仍吐，
    # 因此关思考的硬保证落在工作台 SSE 过滤，而不是上游自律。
    on_up = 0
    for _ in range(3):
        on_up = await _upstream_reasoning_chunks(thinking_type="enabled")
        if on_up >= 1:
            break
    assert on_up >= 1, f"上游 thinking=enabled 应有 reasoning，实际={on_up}"

    try:
        async with httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        ) as client:
            health = await client.get("/healthz")
            if health.status_code != 200:
                pytest.skip(f"后端未就绪 status={health.status_code}")

            # 关思考：即便上游仍吐 reasoning_content，工作台也不得出 thinking_delta
            for _ in range(2):
                off = await _one_turn(client, thinking_enabled=False)
                assert off["thinking_n"] == 0, off
                assert off["assistant_n"] >= 1, off

            # 开思考：上游偶发不吐 reasoning，最多重试 3 次
            on = None
            for _ in range(3):
                candidate = await _one_turn(client, thinking_enabled=True)
                on = candidate
                if int(candidate["thinking_n"]) >= 1:
                    break
            assert on is not None
            assert int(on["thinking_n"]) >= 1, on
            assert int(on["assistant_n"]) >= 1, on
    except httpx.ConnectError as exc:
        pytest.skip(f"无法连接后端 {base}: {exc}")
