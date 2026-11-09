"""真实 PG：登录、建用户、角色拒绝。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_login_and_create_developer(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        health = await client.get("/healthz")
        if health.status_code != 200:
            pytest.skip(f"后端未就绪 status={health.status_code}")

        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        email = f"dev_{uuid.uuid4().hex[:8]}@local"
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "Dev",
                "password": "dev-pass-123",
                "role": "developer",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["email"] == email
        assert body["role"] == "developer"

        # viewer 不可建用户：先建 viewer 再登录
        vemail = f"view_{uuid.uuid4().hex[:8]}@local"
        vcreate = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": vemail,
                "display_name": "Viewer",
                "password": "view-pass-123",
                "role": "viewer",
            },
        )
        assert vcreate.status_code == 200, vcreate.text
        vlogin = await client.post(
            "/api/v1/auth/login",
            json={"email": vemail, "password": "view-pass-123"},
        )
        assert vlogin.status_code == 200, vlogin.text
        vtoken = vlogin.json()["access_token"]
        denied = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {vtoken}"},
            json={
                "email": f"x_{uuid.uuid4().hex[:6]}@local",
                "display_name": "X",
                "password": "x-pass-123",
                "role": "developer",
            },
        )
        assert denied.status_code == 403
