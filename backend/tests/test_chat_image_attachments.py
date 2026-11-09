"""真实会话：上传图片后消息应带回附件，并组装多模态 parts。"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from app.adk_runtime.stream_bridge import _user_content_with_images


ROOT = Path(__file__).resolve().parents[1]

# 1x1 蓝色 PNG
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png_bytes() -> bytes:
    return base64.b64decode(_PNG_B64)


def test_user_content_with_images_builds_inline_parts() -> None:
    raw = _png_bytes()
    content = _user_content_with_images("识别下文字", [(raw, "image/png")])
    assert content.role == "user"
    assert content.parts is not None
    assert len(content.parts) == 2
    assert content.parts[0].text == "识别下文字"
    assert content.parts[1].inline_data is not None
    assert content.parts[1].inline_data.mime_type == "image/png"
    assert content.parts[1].inline_data.data == raw


@pytest.mark.asyncio
async def test_upload_image_persists_on_user_message() -> None:
    from tests.conftest import load_env, login_access_token

    env = load_env()
    host = env.get("HOST", "127.0.0.1")
    port = env.get("PORT", "18080")
    base = f"http://{host}:{port}"
    png = _png_bytes()

    try:
        async with httpx.AsyncClient(base_url=base, timeout=120.0) as client:
            health = await client.get("/healthz")
            if health.status_code != 200:
                pytest.skip(f"后端未就绪 status={health.status_code}")
            token = await login_access_token(client, env)
            headers = {"Authorization": f"Bearer {token}"}

            cr = await client.post(
                "/api/v1/chat/sessions",
                headers=headers,
                json={
                    "title": "image-attach-test",
                    "provider": "custom",
                    "model": "workbuddy/glm-5.3-flash",
                    "agent_id": "default",
                },
            )
            cr.raise_for_status()
            session_id = cr.json()["id"]

            up = await client.post(
                f"/api/v1/chat/sessions/{session_id}/attachments",
                headers=headers,
                files={"file": ("ocr.png", png, "image/png")},
            )
            up.raise_for_status()
            attachment_id = up.json()["id"]
            assert isinstance(attachment_id, str) and attachment_id != ""

            # 只跑到用户消息落库即可：用短超时打断长推理也可，但这里正常跑完一轮
            async with client.stream(
                "POST",
                f"/api/v1/chat/sessions/{session_id}/messages/stream",
                headers=headers,
                json={
                    "content": "识别下文字",
                    "thinking_enabled": False,
                    "web_search_enabled": False,
                    "mentions": [],
                    "attachment_ids": [attachment_id],
                },
            ) as r:
                r.raise_for_status()
                async for _line in r.aiter_lines():
                    pass

            hist = await client.get(
                f"/api/v1/chat/sessions/{session_id}/messages",
                headers=headers,
            )
            hist.raise_for_status()
            items = hist.json()["items"]
            user_msgs = [m for m in items if m.get("role") == "user"]
            assert len(user_msgs) >= 1, items
            last_user = user_msgs[-1]
            assert last_user.get("content") == "识别下文字"
            attachments = last_user.get("attachments")
            assert isinstance(attachments, list) and len(attachments) == 1, last_user
            assert attachments[0]["id"] == attachment_id
            assert attachments[0]["mime_type"] == "image/png"
    except httpx.ConnectError as exc:
        pytest.skip(f"无法连接后端 {base}: {exc}")
