"""真实 PG：凭证轮换、影响面、健康自检与脱敏导出。

凭证域现在只有管理员能进：创建/轮换/导出/删除都要 403 挡住非管理员。
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import login_access_token


async def _admin_headers(client: httpx.AsyncClient, env: dict[str, str]) -> dict[str, str]:
    token = await login_access_token(client, env)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_rotate_secret_and_export_inventory(env: dict[str, str], api_base: str) -> None:
    name = f"rot-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        headers = await _admin_headers(client, env)

        created = await client.post(
            "/api/v1/secrets",
            headers=headers,
            json={
                "name": name,
                "type": "custom",
                "description": "轮换向导集成测试",
                "fields": {"GITHUB_TOKEN": "old-token-value", "org": "acme"},
            },
        )
        assert created.status_code == 200, created.text
        secret_id = created.json()["id"]
        assert created.json()["field_keys"] == ["GITHUB_TOKEN", "org"]

        try:
            health = await client.get("/api/v1/secrets/health", headers=headers)
            assert health.status_code == 200, health.text
            item = next(i for i in health.json()["items"] if i["id"] == secret_id)
            assert item["ok"] is True, item

            usage = await client.get(f"/api/v1/secrets/{secret_id}/usage", headers=headers)
            assert usage.status_code == 200, usage.text
            assert usage.json()["total"] == 0

            # 只换一个字段：未提交的字段必须保持原值（增量轮换）。
            rotated = await client.post(
                f"/api/v1/secrets/{secret_id}/rotate",
                headers=headers,
                json={"fields": {"GITHUB_TOKEN": "new-token-value"}},
            )
            assert rotated.status_code == 200, rotated.text
            body = rotated.json()
            assert body["field_keys"] == ["GITHUB_TOKEN", "org"]
            assert body["updated_at"] >= created.json()["updated_at"]

            rows = await client.get(
                "/api/v1/audit",
                headers=headers,
                params={"action": "secret.rotate", "resource_id": str(secret_id)},
            )
            assert rows.status_code == 200, rows.text
            items = rows.json()["items"]
            assert len(items) == 1, items
            detail = items[0]["detail"]
            assert detail["name"] == name
            assert detail["rotated_fields"] == ["GITHUB_TOKEN"]
            # 审计只记字段名，绝不落新值
            assert "new-token-value" not in json.dumps(detail, ensure_ascii=False)

            # 空 fields 显式失败，不能静默当成功
            empty = await client.post(
                f"/api/v1/secrets/{secret_id}/rotate",
                headers=headers,
                json={"fields": {}},
            )
            assert empty.status_code == 400, empty.text

            unknown = await client.post(
                "/api/v1/secrets/999999/rotate",
                headers=headers,
                json={"fields": {"token": "x"}},
            )
            assert unknown.status_code == 404, unknown.text

            exported = await client.get("/api/v1/secrets/export", headers=headers)
            assert exported.status_code == 200, exported.text
            assert exported.headers["content-type"].startswith("text/csv")
            text = exported.content.decode("utf-8")
            assert text.startswith("\ufeff")
            assert "名称,类型,说明,字段" in text
            assert name in text
            assert "GITHUB_TOKEN,org" in text
            # 清单只有元数据：新旧值都不得出现在导出里
            assert "old-token-value" not in text
            assert "new-token-value" not in text

            export_audit = await client.get(
                "/api/v1/audit",
                headers=headers,
                params={"action": "secret.export"},
            )
            assert export_audit.status_code == 200, export_audit.text
            assert export_audit.json()["total"] >= 1
        finally:
            deleted = await client.delete(
                f"/api/v1/secrets/{secret_id}", headers=headers
            )
            assert deleted.status_code == 200, deleted.text


@pytest.mark.asyncio
async def test_secret_rotation_requires_admin(env: dict[str, str], api_base: str) -> None:
    email = f"rotviewer_{uuid.uuid4().hex[:6]}@local"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        headers = await _admin_headers(client, env)

        # 走 users API 建一个只读账号，验证凭证域整体收口到管理员
        created_user = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": email,
                "display_name": "Rot Viewer",
                "password": "view-pass-123",
                "role": "viewer",
            },
        )
        assert created_user.status_code == 200, created_user.text
        user_id = created_user.json()["id"]
        try:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "view-pass-123"}
            )
            assert login.status_code == 200, login.text
            viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            # 元数据只读：MCP 配置需要凭证名，允许登录用户查看名称与字段名。
            for path in ("/api/v1/secrets", "/api/v1/credential-types"):
                resp = await client.get(path, headers=viewer_headers)
                assert resp.status_code == 200, (path, resp.status_code, resp.text)
            # 健康自检、清单导出、写入动作全部收口到管理员。
            for path in ("/api/v1/secrets/health", "/api/v1/secrets/export"):
                resp = await client.get(path, headers=viewer_headers)
                assert resp.status_code == 403, (path, resp.status_code, resp.text)
            created_secret = await client.post(
                "/api/v1/secrets",
                headers=viewer_headers,
                json={
                    "name": f"viewer-{uuid.uuid4().hex[:6]}",
                    "type": "custom",
                    "description": "viewer must not create",
                    "fields": {"token": "x"},
                },
            )
            assert created_secret.status_code == 403, created_secret.text
        finally:
            await client.delete(f"/api/v1/users/{user_id}", headers=headers)


@pytest.mark.asyncio
async def test_settings_snapshot_masks_secrets(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        headers = await _admin_headers(client, env)

        exported = await client.get("/api/v1/system-settings/export", headers=headers)
        assert exported.status_code == 200, exported.text
        text = exported.content.decode("utf-8")
        assert text.startswith("\ufeff")
        assert "分类,键,值,敏感" in text

        # llm 分类里的 *_api_key 必须是 ***，不能出现种子密钥明文
        api_key_rows = [
            line
            for line in text.splitlines()
            if line.startswith("llm,") and "_api_key" in line
        ]
        if api_key_rows:
            assert all(line.split(",")[2] == "***" for line in api_key_rows), api_key_rows
            assert all(line.split(",")[3] == "是" for line in api_key_rows), api_key_rows

        seed_key = env.get("SEED_LLM_API_KEY", "")
        if seed_key != "":
            assert seed_key not in text

        audits = await client.get(
            "/api/v1/audit", headers=headers, params={"action": "setting.export"}
        )
        assert audits.status_code == 200, audits.text
        assert audits.json()["total"] >= 1


@pytest.mark.asyncio
async def test_audit_export_redacts_stored_detail(env: dict[str, str], api_base: str) -> None:
    """审计里即便存进了带凭据的 URL，导出也必须遮蔽（历史脏数据兜底）。"""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.models_orm.audit import AuditLog

    resource_id = f"redact-{uuid.uuid4().hex[:8]}"
    detail = {
        "endpoint": "https://user:pw@mcp.example.com/sse?token=abc123xyz",
        "api_key": "sk-live-abcdefg",
        "note": "plain-text",
    }

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(
                AuditLog(
                    actor_user_id=None,
                    action="test.redact",
                    resource_type="test_redact",
                    resource_id=resource_id,
                    detail_json=json.dumps(detail, ensure_ascii=False),
                    request_id="req-redact-test",
                )
            )
            await db.commit()

        async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
            if (await client.get("/healthz")).status_code != 200:
                pytest.skip("后端未就绪")
            headers = await _admin_headers(client, env)

            res = await client.get(
                "/api/v1/audit/export",
                headers=headers,
                params={"resource_type": "test_redact", "resource_id": resource_id},
            )
            assert res.status_code == 200, res.text
            text = res.content.decode("utf-8")
            assert resource_id in text
            assert "sk-live-abcdefg" not in text
            assert "abc123xyz" not in text
            assert "pw@" not in text.replace("user:***@", "")
            assert "***" in text
            assert "plain-text" in text

            # 脱敏没有关闭开关：即便显式传 redact=false，出库口径也不变。
            unredacted = await client.get(
                "/api/v1/audit/export",
                headers=headers,
                params={
                    "resource_type": "test_redact",
                    "resource_id": resource_id,
                    "redact": "false",
                },
            )
            assert unredacted.status_code == 200, unredacted.text
            raw_text = unredacted.content.decode("utf-8")
            assert "sk-live-abcdefg" not in raw_text
            assert "abc123xyz" not in raw_text
            assert "***" in raw_text

            # 导出动作自身也要留痕
            audits = await client.get(
                "/api/v1/audit", headers=headers, params={"action": "audit.export"}
            )
            assert audits.status_code == 200, audits.text
            assert audits.json()["total"] >= 1
    finally:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            row = await db.scalar(
                select(AuditLog).where(AuditLog.resource_id == resource_id)
            )
            if row is not None:
                await db.delete(row)
                await db.commit()
        await engine.dispose()
