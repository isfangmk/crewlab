"""MCP OAuth：完整授权码 + PKCE 流程、刷新、手动 Token、state 防护。

本地起真实 OAuth 授权服务器（tests/oauth_stub_server.py，含 RFC 8414/7591
与 PKCE 校验）与真实 MCP Server 子进程，经真实 HTTP API 走全流程；
子进程起不来则 skip（禁 mock）。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from tests.conftest import login_access_token

_AS_SCRIPT = Path(__file__).resolve().parent / "oauth_stub_server.py"
_EXTRAS_SCRIPT = Path(__file__).resolve().parent / "mcp_extras_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn(script: Path, args: list[str]) -> subprocess.Popen | None:
    """起子进程并等端口就绪；15 秒不通返回 None（调用方 skip）。"""

    proc = subprocess.Popen(
        [sys.executable, str(script), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        try:
            with socket.create_connection(("127.0.0.1", int(args[0])), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.3)
    proc.terminate()
    return None


def _kill(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


async def _login(client: httpx.AsyncClient, env: dict[str, str]) -> dict[str, str]:
    token = await login_access_token(client, env)
    return {"Authorization": f"Bearer {token}"}


async def _create_server(
    client: httpx.AsyncClient, headers: dict[str, str], endpoint: str
) -> tuple[int, str]:
    name = f"mcp-oauth-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/mcp/servers",
        headers=headers,
        json={
            "name": name,
            "transport": "sse",
            "endpoint": endpoint,
            "credential": "",
            "header_template_json": "{}",
            "trusted": True,
            "enabled": True,
            "approval_policy": "always",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], name


async def _ensure_headers(server_id: int) -> dict[str, str] | None:
    """直调服务层取 Authorization 头（不连远端）。"""

    from app.db import SessionLocal
    from app.services.mcp import mcp_oauth_service
    from app.services.mcp import mcp_service

    async with SessionLocal() as db:
        server = await mcp_service.get_server(db, server_id)
        return await mcp_oauth_service.ensure_auth_headers(db, server)


async def _force_expire_token(server_id: int) -> None:
    """把落库 token 改成已过期，触发刷新分支。"""

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.mcp_oauth import McpOauthToken
    from app.services.platform.secrets_service import decrypt_json, encrypt_json

    async with SessionLocal() as db:
        row = await db.scalar(
            select(McpOauthToken).where(McpOauthToken.server_id == server_id)
        )
        assert row is not None
        payload = decrypt_json(row.payload_ciphertext)
        payload["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()
        row.payload_ciphertext = encrypt_json(payload)
        await db.commit()


async def test_oauth_full_flow_refresh_and_state_guard(
    env: dict[str, str], api_base: str
) -> None:
    """授权码 + PKCE 全流程 → 刷新 → state 防护 → 解绑。"""

    as_port = _free_port()
    as_proc = _spawn(_AS_SCRIPT, [str(as_port)])
    if as_proc is None:
        pytest.skip("OAuth 授权服务器子进程起不来")
    as_url = f"http://127.0.0.1:{as_port}"
    extras_port = _free_port()
    extras_proc = _spawn(_EXTRAS_SCRIPT, [str(extras_port), as_url])
    if extras_proc is None:
        _kill(as_proc)
        pytest.skip("MCP 测试服务子进程起不来")
    server_id = 0
    try:
        async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
            if (await client.get("/healthz")).status_code != 200:
                pytest.skip("后端未就绪")
            headers = await _login(client, env)
            server_id, _name = await _create_server(
                client, headers, f"http://127.0.0.1:{extras_port}/sse"
            )

            # 发起授权：发现（资源元数据 → AS 元数据）+ 动态注册 + PKCE
            start = await client.post(
                f"/api/v1/mcp/servers/{server_id}/oauth/start", headers=headers
            )
            assert start.status_code == 200, start.text
            authorize_url = start.json()["authorize_url"]
            assert authorize_url.startswith(f"{as_url}/authorize")
            assert "code_challenge_method=S256" in authorize_url
            assert "state=" in authorize_url

            # 浏览器侧：跟 302 到后端回调，回调内部完成 code 换 token
            done = await client.get(authorize_url, follow_redirects=True)
            assert done.status_code == 200, done.text
            assert "授权完成" in done.text

            status = await client.get(
                f"/api/v1/mcp/servers/{server_id}/oauth/status", headers=headers
            )
            assert status.status_code == 200, status.text
            assert status.json()["authorized"] is True
            assert status.json()["auth_source"] == "oauth"

            # 落库 token（加密）与请求头注入
            assert await _ensure_headers(server_id) == {"Authorization": "Bearer access-1"}

            # 刷新：过期后 ensure 自动走 refresh_token
            await _force_expire_token(server_id)
            assert await _ensure_headers(server_id) == {"Authorization": "Bearer access-2"}
            stats = (await client.get(f"{as_url}/stats")).json()
            assert stats["refresh"] == 1, stats

            # state 防护：伪造 state 显式失败
            bad = await client.get(
                "/api/v1/mcp/oauth/callback",
                params={"code": "x", "state": "no-such-state"},
            )
            assert bad.status_code == 400, bad.text
            assert "state 无效" in bad.text

            # 过期 state 同样失败并被清理
            from app.db import SessionLocal
            from app.models.mcp_oauth import McpOauthState

            async with SessionLocal() as db:
                db.add(
                    McpOauthState(
                        state="expired-state-1",
                        server_id=server_id,
                        code_verifier="v" * 43,
                        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    )
                )
                await db.commit()
            expired = await client.get(
                "/api/v1/mcp/oauth/callback",
                params={"code": "x", "state": "expired-state-1"},
            )
            assert expired.status_code == 400, expired.text

            # 解绑：状态归零、headers 不再注入
            revoke = await client.delete(
                f"/api/v1/mcp/servers/{server_id}/oauth", headers=headers
            )
            assert revoke.status_code == 200, revoke.text
            status = await client.get(
                f"/api/v1/mcp/servers/{server_id}/oauth/status", headers=headers
            )
            assert status.json()["authorized"] is False
            assert await _ensure_headers(server_id) is None
    finally:
        if server_id:
            async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
                try:
                    headers = await _login(client, env)
                    await client.delete(
                        f"/api/v1/mcp/servers/{server_id}", headers=headers
                    )
                except Exception:  # noqa: BLE001 - 清理失败不覆盖原始断言错误
                    pass
        _kill(extras_proc)
        _kill(as_proc)


async def test_manual_token_headers_and_expiry(
    env: dict[str, str], api_base: str
) -> None:
    """手动粘贴 Token：注入 Bearer；过期且无刷新路径时显式报错。"""

    extras_port = _free_port()
    extras_proc = _spawn(_EXTRAS_SCRIPT, [str(extras_port), ""])
    if extras_proc is None:
        pytest.skip("MCP 测试服务子进程起不来")
    server_id = 0
    try:
        async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
            if (await client.get("/healthz")).status_code != 200:
                pytest.skip("后端未就绪")
            headers = await _login(client, env)
            server_id, _name = await _create_server(
                client, headers, f"http://127.0.0.1:{extras_port}/sse"
            )

            saved = await client.post(
                f"/api/v1/mcp/servers/{server_id}/oauth/token",
                headers=headers,
                json={"access_token": "manual-1"},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["authorized"] is True
            assert saved.json()["auth_source"] == "manual"

            assert await _ensure_headers(server_id) == {
                "Authorization": "Bearer manual-1"
            }

            # 手动 token 过期且无 refresh_token：显式失败，不静默放行
            await _force_expire_token(server_id)
            with pytest.raises(ValueError, match="过期"):
                await _ensure_headers(server_id)
    finally:
        if server_id:
            async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
                try:
                    headers = await _login(client, env)
                    await client.delete(
                        f"/api/v1/mcp/servers/{server_id}", headers=headers
                    )
                except Exception:  # noqa: BLE001 - 清理失败不覆盖原始断言错误
                    pass
        _kill(extras_proc)
