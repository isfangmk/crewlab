"""真实 PG：审计日志查询与权限。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token


@pytest.mark.asyncio
async def test_audit_list_filters_and_permissions(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        email = f"audit_{uuid.uuid4().hex[:8]}@local"
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "Audit Target",
                "password": "audit-pass-123",
                "role": "developer",
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]

        listed = await client.get(
            "/api/v1/audit",
            headers=headers,
            params={"action": "user.create", "limit": 20},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        hits = [i for i in body["items"] if i["resource_id"] == str(user_id)]
        assert len(hits) == 1, body
        hit = hits[0]
        assert hit["resource_type"] == "user"
        assert hit["actor_email"] == env.get("AUTH_ADMIN_EMAIL")
        assert hit["detail"]["email"] == email
        assert hit["detail"]["role"] == "developer"
        assert isinstance(hit["request_id"], str)
        assert "user.create" in body["actions"]
        assert "user" in body["resource_types"]

        windowed = await client.get(
            "/api/v1/audit",
            headers=headers,
            params={"action": "user.create", "created_from": "2000-01-01T00:00:00Z"},
        )
        assert windowed.status_code == 200, windowed.text
        assert windowed.json()["total"] >= 1

        bad_sort = await client.get(
            "/api/v1/audit",
            headers=headers,
            params={"sort_dir": "sideways"},
        )
        assert bad_sort.status_code == 400, bad_sort.text

        # 非管理员不可查询审计。
        vemail = f"auditv_{uuid.uuid4().hex[:6]}@local"
        vcreate = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": vemail,
                "display_name": "Audit Viewer",
                "password": "view-pass-123",
                "role": "developer",
            },
        )
        assert vcreate.status_code == 200, vcreate.text
        vlogin = await client.post(
            "/api/v1/auth/login",
            json={"email": vemail, "password": "view-pass-123"},
        )
        assert vlogin.status_code == 200, vlogin.text
        denied = await client.get(
            "/api/v1/audit",
            headers={"Authorization": f"Bearer {vlogin.json()['access_token']}"},
        )
        assert denied.status_code == 403, denied.text
