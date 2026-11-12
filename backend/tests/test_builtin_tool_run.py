"""内置工具手动运行。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token

# 引用来源专用语料：命中后要能取到文档定位（doc_id/path/chunk_index）
_CITATION_DOC = "引用来源专用文档：星链校准包含三颗卫星的相位对齐步骤。"


@pytest.mark.asyncio
async def test_run_query_knowledge(env: dict[str, str], api_base: str) -> None:
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        catalog = await client.get("/api/v1/tools/builtin", headers=headers)
        assert catalog.status_code == 200, catalog.text
        body = catalog.json()
        names = [i["name"] for i in body["items"]]
        assert "query_knowledge" in names
        groups = {g["key"]: g for g in body["groups"]}
        assert groups["knowledge"] == {
            "key": "knowledge",
            "title": "知识检索",
            "title_en": "Knowledge",
        }
        assert groups["workflow"]["title"] == "工作流"
        assert all(i["group"] in ("knowledge", "search", "workflow") for i in body["items"])

        run = await client.post(
            "/api/v1/tools/builtin/query_knowledge/run",
            headers=headers,
            json={"params": {"query": "DNS", "limit": 2}},
        )
        if run.status_code >= 500:
            pytest.skip(f"嵌入/库不可用: {run.text}")
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["tool"] == "query_knowledge"
        assert "result" in body


@pytest.mark.asyncio
async def test_query_knowledge_returns_citation_locator(
    env: dict[str, str], api_base: str
) -> None:
    """知识命中必须带可引用的定位信息，前端来源区据此渲染标题/链接/块序号。"""

    # 每次运行带唯一后缀：库里已有同名历史语料，不加后缀会被旧文档挤出 top-k。
    suffix = uuid.uuid4().hex[:6]
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = await client.post(
            "/api/v1/knowledge/upload",
            headers=headers,
            files={
                "file": (
                    "citation-test.txt",
                    f"{_CITATION_DOC}（批次 {suffix}）".encode("utf-8"),
                    "text/plain",
                )
            },
            data={"path": "测试/引用", "title": "星链校准手册"},
        )
        if uploaded.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {uploaded.text}")
        assert uploaded.status_code == 200, uploaded.text
        doc_id = uploaded.json()["id"]

        run = await client.post(
            "/api/v1/tools/builtin/query_knowledge/run",
            headers=headers,
            json={"params": {"query": f"星链校准 相位对齐 {suffix}", "limit": 5}},
        )
        if run.status_code >= 500:
            pytest.skip(f"嵌入/库不可用: {run.text}")
        assert run.status_code == 200, run.text
        items = run.json()["result"]["items"]
        hit = next((i for i in items if i["doc_id"] == doc_id), None)
        assert hit is not None, f"新上传文档未被检索命中: {items}"
        assert hit["title"] == "星链校准手册"
        assert hit["path"] == "测试/引用"
        assert isinstance(hit["chunk_index"], int)
        assert hit["chunk_index"] >= 0
        assert "星链校准" in hit["excerpt"]
        assert isinstance(hit["score"], float)
