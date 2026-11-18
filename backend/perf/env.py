"""压测专用环境读取与登录（独立于 pytest，绝不打印 token）。

复用 tests/conftest.py 的 env 约定但自成一体，避免压测脚本依赖 pytest 运行时。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"


def load_env() -> dict[str, str]:
    if not ENV_PATH.is_file():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def api_base() -> str:
    env = load_env()
    return f"http://{env.get('HOST', '127.0.0.1')}:{env.get('PORT', '18080')}"


def login_token(client: httpx.Client | httpx.AsyncClient) -> str:
    """同步/异步均可：登录拿 token。失败直接抛，不打印任何凭据。"""

    env = load_env()
    email = env.get("AUTH_ADMIN_EMAIL")
    password = env.get("AUTH_TOKEN")
    if not email or not password:
        raise RuntimeError("缺少 AUTH_ADMIN_EMAIL / AUTH_TOKEN，无法压测")
    resp = client.post(
        f"{api_base()}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败 status={resp.status_code}")
    token = resp.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("登录响应缺少 access_token")
    return token


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise RuntimeError("缺少 perf/manifest.json，请先运行 seed.py 造数据")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(data: dict[str, Any]) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
