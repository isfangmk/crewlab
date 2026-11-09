"""知识库切块与混合检索（真实嵌入）。"""

from __future__ import annotations

import httpx
import pytest

from app.services.knowledge_service import chunk_text, extract_upload_text
from tests.conftest import login_access_token


def test_chunk_text_window() -> None:
    content = "a" * 1000
    parts = chunk_text(content, chunk_size=200, chunk_overlap=20)
    assert len(parts) >= 5
    assert parts[0] == "a" * 200


def test_extract_txt() -> None:
    text = extract_upload_text(filename="a.txt", raw="hello 知识库".encode("utf-8"))
    assert "知识库" in text


@pytest.mark.asyncio
async def test_upload_txt_and_search(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        files = {
            "file": ("hybrid-test.txt", "混合检索专用文档：雷达校准手册".encode("utf-8"), "text/plain"),
        }
        up = await client.post(
            "/api/v1/knowledge/upload",
            headers=headers,
            files=files,
            data={"path": "测试", "title": "hybrid-test"},
        )
        if up.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {up.text}")
        assert up.status_code == 200, up.text

        search = await client.get(
            "/api/v1/knowledge/search",
            headers=headers,
            params={"q": "雷达校准", "limit": 5},
        )
        assert search.status_code == 200, search.text
        items = search.json()["items"]
        assert len(items) >= 1
