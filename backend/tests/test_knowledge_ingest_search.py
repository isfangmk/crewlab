"""知识库切块与混合检索（真实嵌入）。"""

from __future__ import annotations

import uuid

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
        doc = up.json()

        try:
            search = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": "雷达校准", "limit": 5},
            )
            assert search.status_code == 200, search.text
            items = search.json()["items"]
            assert len(items) >= 1
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


async def _upload_doc(client: httpx.AsyncClient, headers: dict[str, str], title: str, content: str) -> dict:
    files = {"file": ("retrieval-test.txt", content.encode("utf-8"), "text/plain")}
    up = await client.post(
        "/api/v1/knowledge/upload",
        headers=headers,
        files=files,
        data={"path": "测试", "title": title},
    )
    if up.status_code >= 500:
        pytest.skip(f"嵌入服务不可用: {up.text}")
    assert up.status_code == 200, up.text
    return up.json()


@pytest.mark.asyncio
async def test_search_returns_matched_chunk(env: dict[str, str], api_base: str) -> None:
    """引用摘要取命中块，而不是固定取文档首块。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        marker = f"命中块定位{uuid.uuid4().hex[:8]}"
        content = "填充段落。" * 300 + marker + "。" + "填充段落。" * 300
        doc = await _upload_doc(client, headers, marker, content)
        try:
            search = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": marker, "limit": 5},
            )
            assert search.status_code == 200, search.text
            hits = [i for i in search.json()["items"] if i["doc"]["id"] == doc["id"]]
            assert len(hits) == 1
            assert marker in hits[0]["excerpt"]
            assert hits[0]["chunk_index"] > 0
            # 分道得分：向量通道为余弦相似度，关键词通道为分道内排名
            assert isinstance(hits[0]["vector_score"], float)
            assert 0.0 <= hits[0]["vector_score"] <= 1.0
            assert isinstance(hits[0]["keyword_rank"], int)
            assert hits[0]["keyword_rank"] >= 1
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


@pytest.mark.asyncio
async def test_rrf_min_score_filters_hits(env: dict[str, str], api_base: str) -> None:
    """RRF 最低分阈值：低于阈值的命中被丢弃，非法值显式报错。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        marker = f"阈值过滤{uuid.uuid4().hex[:8]}"
        doc = await _upload_doc(client, headers, marker, f"阈值验证文档：{marker}")
        setting_url = "/api/v1/system-settings/knowledge/rrf_min_score"

        async def hit_ids() -> list[str]:
            res = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": marker, "limit": 10},
            )
            assert res.status_code == 200, res.text
            return [i["doc"]["id"] for i in res.json()["items"]]

        try:
            # 知识库分类必须可读：检索设置弹窗依赖它加载三项参数
            listed = await client.get(
                "/api/v1/system-settings", headers=headers, params={"category": "knowledge"}
            )
            assert listed.status_code == 200, listed.text
            listed_keys = {i["key"] for i in listed.json()["items"]}
            assert {"chunk_size", "chunk_overlap", "rrf_min_score"} <= listed_keys

            # 非数字阈值必须显式失败，不能静默当作 0
            bad = await client.put(setting_url, headers=headers, json={"value": "abc"})
            assert bad.status_code == 400, bad.text

            assert doc["id"] in await hit_ids()

            # 该测试文档只有一个切块：双通道 RRF 总分约 0.0328，低于 0.05 应被丢弃
            put = await client.put(setting_url, headers=headers, json={"value": "0.05"})
            assert put.status_code == 200, put.text
            assert doc["id"] not in await hit_ids()

            restored = await client.put(setting_url, headers=headers, json={"value": "0"})
            assert restored.status_code == 200, restored.text
            assert doc["id"] in await hit_ids()
        finally:
            await client.put(setting_url, headers=headers, json={"value": "0"})
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


@pytest.mark.asyncio
async def test_search_source_type_filter(env: dict[str, str], api_base: str) -> None:
    """来源筛选：按 source_type 收口检索，非法来源显式 400。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        marker = f"来源筛选{uuid.uuid4().hex[:8]}"
        doc = await _upload_doc(client, headers, marker, f"来源验证文档：{marker}")

        async def hit_ids(source_type: str | None) -> list[str]:
            params: dict[str, str | int] = {"q": marker, "limit": 10}
            if source_type is not None:
                params["source_type"] = source_type
            res = await client.get("/api/v1/knowledge/search", headers=headers, params=params)
            assert res.status_code == 200, res.text
            return [i["doc"]["id"] for i in res.json()["items"]]

        try:
            # 上传入库的文档 source_type=upload：只在 upload 口径下命中
            assert doc["source_type"] == "upload"
            assert doc["id"] in await hit_ids("upload")
            assert doc["id"] not in await hit_ids("manual")
            assert doc["id"] in await hit_ids(None)

            # 不在取值集合里的来源直接拒绝，不做静默降级为「全部来源」
            bad = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": marker, "source_type": "wiki"},
            )
            assert bad.status_code == 400, bad.text
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


@pytest.mark.asyncio
async def test_disabled_doc_excluded_from_search(env: dict[str, str], api_base: str) -> None:
    """停用后不参与检索，但文档与切块保留；重新启用后恢复命中。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        marker = f"启停过滤{uuid.uuid4().hex[:8]}"
        doc = await _upload_doc(client, headers, marker, f"启停验证文档：{marker}")
        try:
            async def hit_ids() -> list[str]:
                res = await client.get(
                    "/api/v1/knowledge/search",
                    headers=headers,
                    params={"q": marker, "limit": 10},
                )
                assert res.status_code == 200, res.text
                return [i["doc"]["id"] for i in res.json()["items"]]

            assert doc["id"] in await hit_ids()

            off = await client.patch(
                f"/api/v1/knowledge/docs/{doc['id']}/enabled",
                headers=headers,
                json={"enabled": False},
            )
            assert off.status_code == 200, off.text
            assert off.json()["enabled"] is False
            assert doc["id"] not in await hit_ids()

            listing = await client.get("/api/v1/knowledge/docs", headers=headers, params={"limit": 200})
            assert listing.status_code == 200, listing.text
            assert any(d["id"] == doc["id"] for d in listing.json()["items"])

            on = await client.patch(
                f"/api/v1/knowledge/docs/{doc['id']}/enabled",
                headers=headers,
                json={"enabled": True},
            )
            assert on.status_code == 200, on.text
            assert on.json()["enabled"] is True
            assert doc["id"] in await hit_ids()
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)
