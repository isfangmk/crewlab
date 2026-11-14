"""对外发布：渠道 CRUD/Key 鉴权 + OpenAI 兼容端点（真实模型调用）。

渠道会话复用同一 ChatSession，观测按渠道名记账；流式/非流式各消耗一轮对话。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import create_db_engine
from app.models_orm.channel import PublishedChannel
from app.models_orm.observation import ObservationTurn
from app.services import chat_service
from tests.conftest import login_access_token


def _channel_body(name: str, expires_in_hours: int = 0) -> dict:
    return {
        "name": name,
        "agent_id": "default",
        "provider": "",
        "model": "",
        "expires_in_hours": expires_in_hours,
    }


async def test_channel_lifecycle_and_key_auth(env, api_base) -> None:
    """创建/列表/停用/轮换/删除，以及错误 Key 与停用渠道的 401。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        name = f"pub-{uuid.uuid4().hex[:8]}"

        created = await client.post(
            "/api/v1/publish/channels", headers=headers, json=_channel_body(name)
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["name"] == name
        assert body["key"].startswith("pk-")
        assert body["key_masked"].endswith("…")
        assert body["enabled"] is True
        channel_id = body["id"]

        listed = await client.get("/api/v1/publish/channels", headers=headers)
        assert listed.status_code == 200, listed.text
        hit = next(item for item in listed.json()["items"] if item["id"] == channel_id)
        assert hit["usage"]["turns"] == 0

        try:
            # 错误 Key：401
            bad = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer pk-not-a-real-key"},
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            assert bad.status_code == 401, bad.text

            # 缺少 Authorization：401
            missing = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            assert missing.status_code == 401, missing.text

            # 停用后原 Key 401；重新启用后恢复
            off = await client.patch(
                f"/api/v1/publish/channels/{channel_id}",
                headers=headers,
                json={"enabled": False},
            )
            assert off.status_code == 200, off.text
            blocked = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {body['key']}"},
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            assert blocked.status_code == 401, blocked.text
            assert "停用" in blocked.text

            on = await client.patch(
                f"/api/v1/publish/channels/{channel_id}",
                headers=headers,
                json={"enabled": True},
            )
            assert on.status_code == 200, on.text

            # 轮换：旧 Key 立即失效
            rotated = await client.post(
                f"/api/v1/publish/channels/{channel_id}/rotate", headers=headers
            )
            assert rotated.status_code == 200, rotated.text
            new_key = rotated.json()["key"]
            assert new_key != body["key"]
            stale = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {body['key']}"},
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            assert stale.status_code == 401, stale.text
        finally:
            deleted = await client.delete(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
            assert deleted.status_code == 200, deleted.text
            gone = await client.delete(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
            assert gone.status_code == 404, gone.text


async def test_expired_channel_rejected(env, api_base) -> None:
    """过期渠道即使 Key 正确也 401（不触发模型调用）。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        name = f"expired-{uuid.uuid4().hex[:8]}"
        created = await client.post(
            "/api/v1/publish/channels",
            headers=headers,
            json=_channel_body(name, expires_in_hours=1),
        )
        assert created.status_code == 200, created.text
        body = created.json()

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                row = await db.scalar(
                    select(PublishedChannel).where(PublishedChannel.id == body["id"])
                )
                assert row is not None
                row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                await db.commit()
        finally:
            await engine.dispose()

        try:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {body['key']}"},
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            assert resp.status_code == 401, resp.text
            assert "过期" in resp.text
        finally:
            await client.delete(
                f"/api/v1/publish/channels/{body['id']}", headers=headers
            )


async def test_openai_compatible_calls_and_usage(env, api_base) -> None:
    """非流式/流式各一轮真实对话，并验证 /v1/models 与按渠道用量。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=180.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        name = f"openai-{uuid.uuid4().hex[:8]}"
        created = await client.post(
            "/api/v1/publish/channels", headers=headers, json=_channel_body(name)
        )
        assert created.status_code == 200, created.text
        body = created.json()
        channel_id = body["id"]
        key = body["key"]
        auth = {"Authorization": f"Bearer {key}"}

        try:
            models = await client.get("/v1/models", headers=auth)
            assert models.status_code == 200, models.text
            assert models.json()["data"][0]["id"] == body["model"]

            plain = await client.post(
                "/v1/chat/completions",
                headers=auth,
                json={
                    "model": body["model"],
                    "messages": [{"role": "user", "content": "只回复两个字：收到"}],
                },
            )
            assert plain.status_code == 200, plain.text
            completion = plain.json()
            assert completion["object"] == "chat.completion"
            assert completion["choices"][0]["message"]["role"] == "assistant"
            assert completion["choices"][0]["message"]["content"].strip() != ""
            assert completion["choices"][0]["finish_reason"] == "stop"

            chunks: list[dict] = []
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers=auth,
                json={
                    "model": body["model"],
                    "messages": [{"role": "user", "content": "只回复两个字：好的"}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            ) as resp:
                assert resp.status_code == 200, resp.status_code
                saw_done = False
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        saw_done = True
                        break
                    chunks.append(json.loads(payload))
            assert saw_done
            assert chunks[0]["object"] == "chat.completion.chunk"
            text = "".join(
                choice["delta"].get("content", "")
                for chunk in chunks
                for choice in chunk.get("choices", [])
                if isinstance(choice.get("delta"), dict)
            )
            assert text.strip() != ""
            assert any(
                chunk.get("choices")
                and chunk["choices"][0].get("finish_reason") == "stop"
                for chunk in chunks
            )
            # include_usage：普通块带 usage=null，最后一帧是空 choices 的用量块
            assert all("usage" in chunk for chunk in chunks), chunks
            usage_chunks = [chunk for chunk in chunks if chunk["usage"] is not None]
            assert len(usage_chunks) == 1, usage_chunks
            assert chunks[-1] is usage_chunks[0]
            assert chunks[-1]["choices"] == []
            usage = chunks[-1]["usage"]
            assert usage["total_tokens"] >= usage["prompt_tokens"]

            listed = await client.get("/api/v1/publish/channels", headers=headers)
            assert listed.status_code == 200, listed.text
            hit = next(item for item in listed.json()["items"] if item["id"] == channel_id)
            assert hit["usage"]["turns"] >= 2
            assert hit["usage"]["last_used_at"] is not None
            assert hit["session_id"] is not None

            # 观测按渠道过滤：两轮调用都归属该渠道，渠道候选值里能选到
            obs = await client.get(
                "/api/v1/observability/turns",
                params={"channel": name, "limit": 20},
                headers=headers,
            )
            assert obs.status_code == 200, obs.text
            assert obs.json()["total"] >= 2
            assert all(row["channel"] == name for row in obs.json()["items"])
            obs_overview = await client.get(
                "/api/v1/observability/overview",
                params={"channel": name},
                headers=headers,
            )
            assert obs_overview.status_code == 200, obs_overview.text
            assert obs_overview.json()["stats"]["total"] == obs.json()["total"]
            assert name in obs_overview.json()["channels"]

            detail = await client.get(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["usage"] == hit["usage"]
        finally:
            session_id = None
            detail = await client.get(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
            if detail.status_code == 200:
                session_id = detail.json().get("session_id")
            await client.delete(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
            # 删渠道不级联会话/观测：测试自己收尾，避免留下发布渠道的会话与用量
            engine = create_db_engine(get_settings().database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    if isinstance(session_id, str) and session_id != "":
                        await chat_service.delete_session(db, session_id)
                    await db.execute(
                        delete(ObservationTurn).where(ObservationTurn.channel == name)
                    )
                    await db.commit()
            finally:
                await engine.dispose()


async def test_channel_edit_and_usage_rename(env, api_base) -> None:
    """编辑渠道：删改字段合并生效，改名同步观测用量的渠道归属。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        old_name = f"edit-old-{uuid.uuid4().hex[:8]}"
        new_name = f"edit-new-{uuid.uuid4().hex[:8]}"
        created = await client.post(
            "/api/v1/publish/channels", headers=headers, json=_channel_body(old_name)
        )
        assert created.status_code == 200, created.text
        body = created.json()
        channel_id = body["id"]

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    ObservationTurn(
                        id=uuid.uuid4().hex,
                        session_id=f"obs-{uuid.uuid4().hex[:12]}",
                        agent_id=body["agent_id"],
                        provider=body["provider"],
                        model=body["model"],
                        status="ok",
                        request_id="",
                        channel=old_name,
                        input_summary="渠道改名迁移测试",
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                    )
                )
                await db.commit()

            try:
                detail = await client.get(
                    f"/api/v1/publish/channels/{channel_id}", headers=headers
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["usage"]["turns"] == 1
                assert detail.json()["usage"]["total_tokens"] == 15

                # provider/model 是整体配置，只改一半必须显式失败
                pair = await client.patch(
                    f"/api/v1/publish/channels/{channel_id}",
                    headers=headers,
                    json={"provider": body["provider"]},
                )
                assert pair.status_code == 400, pair.text

                updated = await client.patch(
                    f"/api/v1/publish/channels/{channel_id}",
                    headers=headers,
                    json={"name": new_name, "enabled": False},
                )
                assert updated.status_code == 200, updated.text
                payload = updated.json()
                assert payload["name"] == new_name
                assert payload["enabled"] is False
                assert payload["agent_id"] == body["agent_id"]
                assert payload["usage"]["turns"] == 1
                assert payload["usage"]["last_used_at"] is not None
            finally:
                await client.delete(
                    f"/api/v1/publish/channels/{channel_id}", headers=headers
                )

            # 历史回合的渠道名跟着改，按新名查得到、旧名查不到
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                new_rows = (
                    await db.scalars(
                        select(ObservationTurn).where(ObservationTurn.channel == new_name)
                    )
                ).all()
                old_rows = (
                    await db.scalars(
                        select(ObservationTurn).where(ObservationTurn.channel == old_name)
                    )
                ).all()
                assert len(new_rows) == 1
                assert old_rows == []
        finally:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await db.execute(
                    delete(ObservationTurn).where(
                        ObservationTurn.channel.in_([old_name, new_name])
                    )
                )
                await db.commit()
            await engine.dispose()


async def test_openai_error_shapes_and_request_validation(env, api_base) -> None:
    """错误响应走 OpenAI 结构；无效参数在请求边界显式 400/404。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        name = f"errors-{uuid.uuid4().hex[:8]}"
        created = await client.post(
            "/api/v1/publish/channels", headers=headers, json=_channel_body(name)
        )
        assert created.status_code == 200, created.text
        body = created.json()
        channel_id = body["id"]
        auth = {"Authorization": f"Bearer {body['key']}"}

        def assert_openai_error(resp: httpx.Response, status: int) -> dict:
            assert resp.status_code == status, resp.text
            payload = resp.json()
            assert set(payload) == {"error"}
            assert isinstance(payload["error"]["message"], str)
            assert payload["error"]["message"] != ""
            assert "type" in payload["error"]
            return payload["error"]

        try:
            assert_openai_error(
                await client.get(
                    "/v1/models", headers={"Authorization": "Bearer pk-bogus"}
                ),
                401,
            )
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "你好"}]},
                ),
                401,
            )
            # 请求体类型错误：FastAPI 默认 422，公开端点转成 OpenAI 400
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={"messages": "not-a-list"},
                ),
                400,
            )
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "messages": [{"role": "user", "content": "你好"}],
                        "n": 2,
                    },
                ),
                400,
            )
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "messages": [{"role": "user", "content": "你好"}],
                        "temperature": 0.2,
                        "top_p": 0.9,
                    },
                ),
                400,
            )
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "messages": [{"role": "user", "content": "你好"}],
                        "tools": [{"type": "function"}],
                    },
                ),
                400,
            )
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "messages": [{"role": "user", "content": "你好"}],
                        "stream_options": {"include_usage": True},
                    },
                ),
                400,
            )
            unknown = assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "messages": [{"role": "user", "content": "你好"}],
                        "foo": "bar",
                    },
                ),
                400,
            )
            assert "foo" in unknown["message"]
            mismatch = assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "no-such-model",
                        "messages": [{"role": "user", "content": "你好"}],
                    },
                ),
                404,
            )
            assert mismatch["code"] == "not_found"
            assert_openai_error(
                await client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={"messages": [{"role": "assistant", "content": "你好"}]},
                ),
                400,
            )

            # 站内接口保持 FastAPI 默认错误格式
            internal = await client.get(
                "/api/v1/publish/channels/no-such-channel", headers=headers
            )
            assert internal.status_code == 404
            assert "detail" in internal.json()
            assert "error" not in internal.json()
        finally:
            await client.delete(
                f"/api/v1/publish/channels/{channel_id}", headers=headers
            )
