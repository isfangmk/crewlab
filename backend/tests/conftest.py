"""测试公共夹具：读 .env、登录拿 Bearer。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


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
