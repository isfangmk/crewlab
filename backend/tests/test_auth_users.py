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


@pytest.mark.asyncio
async def test_update_role_reset_password_and_delete_user(
    env: dict[str, str], api_base: str
) -> None:
    """改角色、重置密码、删除账号的完整链路与审计留痕。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200, me.text
        admin_id = me.json()["id"]

        email = f"member_{uuid.uuid4().hex[:8]}@local"
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "Member",
                "password": "old-pass-123",
                "role": "developer",
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]

        # 改角色后旧 Token 立即按新角色鉴权。
        member_login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "old-pass-123"}
        )
        assert member_login.status_code == 200, member_login.text
        member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}"}
        assert (await client.get("/api/v1/users", headers=member_headers)).status_code == 403

        role_changed = await client.patch(
            f"/api/v1/users/{user_id}/role", headers=headers, json={"role": "viewer"}
        )
        assert role_changed.status_code == 200, role_changed.text
        assert role_changed.json()["role"] == "viewer"
        # 角色变更即时生效：之前的 developer Token 现在只能读。
        assert (await client.get("/api/v1/users", headers=member_headers)).status_code == 403

        # 重置密码会吊销旧 Token，旧密码失效、新密码可登录。
        reset = await client.post(
            f"/api/v1/users/{user_id}/password",
            headers=headers,
            json={"password": "new-pass-456"},
        )
        assert reset.status_code == 204, reset.text
        assert (await client.get("/api/v1/me", headers=member_headers)).status_code == 401
        old_login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "old-pass-123"}
        )
        assert old_login.status_code == 401
        new_login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "new-pass-456"}
        )
        assert new_login.status_code == 200, new_login.text

        # 删除后账号不可登录，也不在列表中。
        deleted = await client.delete(f"/api/v1/users/{user_id}", headers=headers)
        assert deleted.status_code == 204, deleted.text
        assert (await client.get("/api/v1/me", headers={
            "Authorization": f"Bearer {new_login.json()['access_token']}"
        })).status_code == 401
        listed = await client.get("/api/v1/users", headers=headers)
        assert listed.status_code == 200
        listed_body = listed.json()
        assert "items" in listed_body and "total" in listed_body, listed.text
        assert all(item["id"] != user_id for item in listed_body["items"])

        # 三个动作都写入审计，且 detail 带可供排查的标识。
        for action in ("user.role", "user.password_reset", "user.delete"):
            audited = await client.get(
                "/api/v1/audit",
                headers=headers,
                params={"action": action, "resource_id": str(user_id), "limit": 10},
            )
            assert audited.status_code == 200, audited.text
            items = audited.json()["items"]
            assert len(items) == 1, f"{action} 审计缺失: {audited.text}"
            assert items[0]["actor_email"] == me.json()["email"]
            assert items[0]["detail"]["email"] == email


@pytest.mark.asyncio
async def test_user_management_guardrails(env: dict[str, str], api_base: str) -> None:
    """非法角色、未知用户和自我操作都必须显式拒绝。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        me = await client.get("/api/v1/me", headers=headers)
        admin_id = me.json()["id"]

        assert (await client.patch(
            f"/api/v1/users/{admin_id}/role", headers=headers, json={"role": "viewer"}
        )).status_code == 400
        assert (await client.delete(f"/api/v1/users/{admin_id}", headers=headers)).status_code == 400

        unknown = 99_999_999
        assert (await client.patch(
            f"/api/v1/users/{unknown}/role", headers=headers, json={"role": "viewer"}
        )).status_code == 404
        assert (await client.post(
            f"/api/v1/users/{unknown}/password", headers=headers, json={"password": "x-pass-123"}
        )).status_code == 404
        assert (await client.delete(f"/api/v1/users/{unknown}", headers=headers)).status_code == 404

        email = f"guard_{uuid.uuid4().hex[:8]}@local"
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "Guard",
                "password": "guard-pass-123",
                "role": "developer",
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        try:
            bad_role = await client.patch(
                f"/api/v1/users/{user_id}/role", headers=headers, json={"role": "owner"}
            )
            assert bad_role.status_code == 400
            empty_password = await client.post(
                f"/api/v1/users/{user_id}/password", headers=headers, json={"password": ""}
            )
            assert empty_password.status_code == 422
        finally:
            await client.delete(f"/api/v1/users/{user_id}", headers=headers)


@pytest.mark.asyncio
async def test_user_list_filters_and_paginates(env: dict[str, str], api_base: str) -> None:
    """用户列表按服务端筛选与分页返回 items 与命中总数。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        # 建 3 个可辨识的账号，筛选结果必须只含目标账号
        created_ids: list[int] = []
        try:
            for idx in range(3):
                email = f"page_{idx}_{uuid.uuid4().hex[:8]}@local"
                created = await client.post(
                    "/api/v1/users",
                    headers=headers,
                    json={
                        "email": email,
                        "display_name": f"Page Target {idx}",
                        "password": "page-pass-123",
                        "role": "viewer",
                    },
                )
                assert created.status_code == 200, created.text
                created_ids.append(created.json()["id"])

            # 关键字筛选：命中邮箱或显示名
            by_keyword = await client.get(
                "/api/v1/users", headers=headers, params={"q": "Page Target 1"}
            )
            assert by_keyword.status_code == 200, by_keyword.text
            body = by_keyword.json()
            assert body["total"] == 1, body
            assert body["items"][0]["display_name"] == "Page Target 1"

            # 角色筛选：只返回 viewer
            by_role = await client.get(
                "/api/v1/users", headers=headers, params={"role": "viewer", "limit": 100}
            )
            assert by_role.status_code == 200, by_role.text
            role_body = by_role.json()
            assert role_body["total"] >= 3, role_body
            assert all(item["role"] == "viewer" for item in role_body["items"])

            # 状态筛选与分页：limit 截断、offset 翻页，total 是命中总数而非本页条数
            first_page = await client.get(
                "/api/v1/users", headers=headers, params={"limit": 2, "offset": 0}
            )
            assert first_page.status_code == 200, first_page.text
            first_body = first_page.json()
            assert len(first_body["items"]) == 2, first_body
            assert first_body["total"] >= 2, first_body

            second_page = await client.get(
                "/api/v1/users", headers=headers, params={"limit": 2, "offset": 2}
            )
            assert second_page.status_code == 200, second_page.text
            second_body = second_page.json()
            assert len(second_body["items"]) == 2, second_body
            first_ids = {item["id"] for item in first_body["items"]}
            second_ids = {item["id"] for item in second_body["items"]}
            assert first_ids.isdisjoint(second_ids), (first_body, second_body)

            # 非法筛选值显式拒绝
            bad_role = await client.get(
                "/api/v1/users", headers=headers, params={"role": "owner"}
            )
            assert bad_role.status_code == 400, bad_role.text
            bad_status = await client.get(
                "/api/v1/users", headers=headers, params={"status": "paused"}
            )
            assert bad_status.status_code == 400, bad_status.text

            # 下拉选项：只返回 id 与邮箱
            options = await client.get("/api/v1/users/options", headers=headers)
            assert options.status_code == 200, options.text
            option_rows = options.json()
            assert option_rows, options.text
            assert set(option_rows[0].keys()) == {"id", "email"}, option_rows
        finally:
            for user_id in created_ids:
                await client.delete(f"/api/v1/users/{user_id}", headers=headers)
