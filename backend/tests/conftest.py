"""测试公共夹具：读 .env、登录拿 Bearer；自动汇总的 LLM 调用统一打桩。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


async def default_llm() -> tuple[str, str]:
    """返回系统当前默认 (provider, model)，真调 LLM 的测试用它，禁止写死模型。

    与新建会话/列表页的默认选择同源（get_default_selection），默认变更时测试自动跟随。
    """

    from app.db import SessionLocal
    from app.services.platform.llm_config import get_default_selection

    async with SessionLocal() as db:
        return await get_default_selection(db)


async def collect_turn_events(
    client: httpx.AsyncClient,
    session_id: str,
    body: dict,
    headers: dict | None = None,
) -> list[tuple[str, dict]]:
    """发起流式回合并收集全部帧，返回 [(event, data), ...]。

    POST /messages/stream 现在只登记回合、立即返回 turn_id，帧不随该响应返回；
    所有订阅者统一走 GET /events?turn_id= 读 Redis 帧流（支持补发+续推）。
    本 helper 按新契约两段式消费，等 done 帧再返回，避免读消息历史时撞落库竞态。
    """

    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages/stream",
        json=body,
        headers=headers,
    )
    resp.raise_for_status()
    turn_id = resp.json()["turn_id"]

    frames: list[tuple[str, dict]] = []
    async with client.stream(
        "GET",
        f"/api/v1/chat/sessions/{session_id}/events",
        params={"turn_id": turn_id},
        headers=headers,
    ) as events:
        events.raise_for_status()
        event_name = ""
        async for line in events.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "":
                continue
            data = json.loads(raw)
            frames.append((event_name, data))
            if event_name == "done":
                break
    return frames


@pytest.fixture(autouse=True)
async def _redis_connection() -> None:
    """直连服务的测试不经过 lifespan：这里显式连接真 Redis，结束关闭。

    强依赖约束不变——业务代码仍要求先 connect 才能 get_redis；只是测试夹具
    代替 lifespan 完成初始化。用的是真 Redis（禁 mock）。
    """

    from app.redis import close, connect

    await connect()
    try:
        yield
    finally:
        await close()


@pytest.fixture(autouse=True)
def _stub_summary_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动汇总的 LLM 调用打桩（沿用执行缝打桩口径），返回拼接降级文本。

    消息落库、幂等、终态事件照常走真实实现；只有模型调用被替换成
    compose_summary_fallback 的确定性输出，测试不打真模型。
    """

    from app.services.chat import auto_summary

    async def fake_generate(
        _db: object,
        *,
        session_id: str,  # pyright: ignore[reportUnusedParameter] — 与 _generate 同签名
        kind: str,  # pyright: ignore[reportUnusedParameter] — 与 _generate 同签名
        question: str,
        items: list[dict],
        gaps: list[str],
    ) -> str:
        return auto_summary.compose_summary_fallback(
            question=question, items=items, gaps=gaps
        )

    monkeypatch.setattr(auto_summary, "_generate", fake_generate)


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@pytest.fixture
def env() -> dict[str, str]:
    return load_env()


@pytest.fixture
def api_base(env: dict[str, str]) -> str:
    host = env.get("HOST", "127.0.0.1")
    port = env.get("PORT", "18080")
    return f"http://{host}:{port}"


async def login_access_token(client: httpx.AsyncClient, env: dict[str, str]) -> str:
    email = env.get("AUTH_ADMIN_EMAIL")
    password = env.get("AUTH_TOKEN")
    if not email:
        pytest.skip("缺少 AUTH_ADMIN_EMAIL")
    if not password:
        pytest.skip("缺少 AUTH_TOKEN")
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if resp.status_code != 200:
        pytest.skip(f"登录失败 status={resp.status_code} body={resp.text}")
    token = resp.json().get("access_token")
    if not isinstance(token, str) or token == "":
        pytest.skip("登录响应缺少 access_token")
    return token
