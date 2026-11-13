"""知识库文档列表接口：服务端筛选 + 分页。

覆盖 GET /knowledge/docs 的 q（标题）/ source_type / builtin / path_prefix /
sort_dir / limit / offset，以及 GET /knowledge/paths 的 builtin 分组口径。
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from tests.conftest import login_access_token


async def _create_doc(client: httpx.AsyncClient, headers: dict[str, str], **body: object) -> dict:
    resp = await client.post("/api/v1/knowledge/docs", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_docs_list_filters_and_paginates(env: dict[str, str], api_base: str) -> None:
    """标题筛选、来源筛选与分页都在服务端生效；total 是命中总数而非当页条数。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        marker = f"筛选{uuid.uuid4().hex[:8]}"
        alpha = await _create_doc(
            client,
            headers,
            title=f"{marker}甲",
            content="正文甲",
            path="筛选目录/甲",
        )
        beta = await _create_doc(
            client,
            headers,
            title=f"{marker}乙",
            content="正文乙",
            path="筛选目录/乙",
        )
        try:
            # 标题关键字：只命中标题里带标记的文档
            by_title = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": marker, "limit": 50},
            )
            assert by_title.status_code == 200, by_title.text
            body = by_title.json()
            assert body["total"] == 2
            assert {d["id"] for d in body["items"]} == {alpha["id"], beta["id"]}

            # 列表附带索引统计：分块数、正文字符/字节数、最后索引时间
            sample = next(d for d in body["items"] if d["id"] == alpha["id"])
            assert sample["chunk_count"] >= 1
            assert sample["content_chars"] == len("正文甲")
            assert sample["content_bytes"] == len("正文甲".encode("utf-8"))
            assert sample["indexed_at"] is not None
            assert sample["source_filename"] is None
            assert sample["hit_count"] >= 0

            # 标题关键字匹配不到：total 为 0，不是全量
            no_hit = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": f"{marker}不存在", "limit": 50},
            )
            assert no_hit.status_code == 200, no_hit.text
            assert no_hit.json()["total"] == 0
            assert no_hit.json()["items"] == []

            # path_prefix 命中本级与子级
            by_path = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"path_prefix": "筛选目录", "limit": 50},
            )
            assert by_path.status_code == 200, by_path.text
            assert by_path.json()["total"] == 2

            # 分页：total 不随当页条数变化
            page1 = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": marker, "limit": 1, "offset": 0},
            )
            page2 = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": marker, "limit": 1, "offset": 1},
            )
            assert page1.status_code == 200 and page2.status_code == 200
            assert page1.json()["total"] == 2
            assert page2.json()["total"] == 2
            assert len(page1.json()["items"]) == 1
            assert len(page2.json()["items"]) == 1
            assert page1.json()["items"][0]["id"] != page2.json()["items"][0]["id"]

            # sort_dir 改变顺序
            asc = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": marker, "sort_dir": "asc", "limit": 50},
            )
            desc = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"q": marker, "sort_dir": "desc", "limit": 50},
            )
            assert asc.status_code == 200 and desc.status_code == 200
            assert [d["id"] for d in asc.json()["items"]] == list(
                reversed([d["id"] for d in desc.json()["items"]])
            )

            # 非法排序方向显式报错
            bad = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"sort_dir": "sideways"},
            )
            assert bad.status_code == 400, bad.text
        finally:
            for doc in (alpha, beta):
                await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


@pytest.mark.asyncio
async def test_docs_list_builtin_groups_are_disjoint(env: dict[str, str], api_base: str) -> None:
    """builtin=true 只取 vault，false 只取组织文档；两棵目录树的计数互不混算。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        marker = f"分组{uuid.uuid4().hex[:8]}"
        org = await _create_doc(
            client,
            headers,
            title=f"{marker}组织文档",
            content="正文",
            path="分组目录",
        )
        try:
            org_only = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"builtin": "false", "q": marker, "limit": 50},
            )
            assert org_only.status_code == 200, org_only.text
            assert org_only.json()["total"] == 1
            assert org_only.json()["items"][0]["id"] == org["id"]

            builtin_only = await client.get(
                "/api/v1/knowledge/docs",
                headers=headers,
                params={"builtin": "true", "q": marker, "limit": 50},
            )
            assert builtin_only.status_code == 200, builtin_only.text
            assert builtin_only.json()["total"] == 0

            # 目录树：按来源组分别聚合，total 是该组的文档总数
            org_paths = await client.get(
                "/api/v1/knowledge/paths",
                headers=headers,
                params={"builtin": "false"},
            )
            assert org_paths.status_code == 200, org_paths.text
            assert org_paths.json()["total"] >= 1
            assert any(p["path"] == "分组目录" for p in org_paths.json()["items"])

            builtin_paths = await client.get(
                "/api/v1/knowledge/paths",
                headers=headers,
                params={"builtin": "true"},
            )
            assert builtin_paths.status_code == 200, builtin_paths.text
            assert all(p["path"] != "分组目录" for p in builtin_paths.json()["items"])
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{org['id']}", headers=headers)


@pytest.mark.asyncio
async def test_docs_list_upload_source_and_hit_stats(
    env: dict[str, str], api_base: str
) -> None:
    """上传原件信息入库；被检索命中后累计 hit_count 与 last_hit_at。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        marker = f"原件{uuid.uuid4().hex[:8]}"
        raw = f"{marker} 正文".encode("utf-8")
        filename = f"{marker}.txt"
        up = await client.post(
            "/api/v1/knowledge/upload",
            headers=headers,
            files={"file": (filename, raw, "text/plain")},
            data={"path": "原件目录", "title": marker},
        )
        if up.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {up.text}")
        assert up.status_code == 200, up.text
        doc = up.json()
        try:
            assert doc["source_filename"] == filename
            assert doc["source_mime"] == "text/plain"
            assert doc["source_bytes"] == len(raw)
            assert doc["hit_count"] == 0

            search = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": marker, "path_prefix": "原件目录", "limit": 5},
            )
            assert search.status_code == 200, search.text
            assert search.json()["total"] >= 1

            # 命中统计在响应后异步写入，轮询等待最终一致
            item: dict = {}
            for _ in range(15):
                row = await client.get(
                    "/api/v1/knowledge/docs",
                    headers=headers,
                    params={"q": marker, "limit": 1},
                )
                assert row.status_code == 200, row.text
                item = row.json()["items"][0]
                if item["hit_count"] >= 1:
                    break
                await asyncio.sleep(0.2)
            assert item["hit_count"] >= 1, item
            assert item["last_hit_at"] is not None, item
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)


@pytest.mark.asyncio
async def test_upload_tags_normalization_and_search_bounds(
    env: dict[str, str], api_base: str
) -> None:
    """上传标签去空白/空项、空标题回退原件名；检索参数有边界且空白词显式失败。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        marker = f"标签{uuid.uuid4().hex[:8]}"
        filename = f"{marker}.txt"
        up = await client.post(
            "/api/v1/knowledge/upload",
            headers=headers,
            files={"file": (filename, f"{marker} 正文".encode("utf-8"), "text/plain")},
            data={
                "path": "标签目录",
                "title": "  ",
                "tags": " 巡检, , 网络，DNS, ",
            },
        )
        if up.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {up.text}")
        assert up.status_code == 200, up.text
        doc = up.json()
        try:
            assert doc["title"] == filename
            assert doc["tags"] == ["巡检", "网络", "DNS"]

            # 边界校验要在调用嵌入服务之前拦住
            blank = await client.get(
                "/api/v1/knowledge/search", headers=headers, params={"q": "   "}
            )
            assert blank.status_code == 400, blank.text
            too_many = await client.get(
                "/api/v1/knowledge/search",
                headers=headers,
                params={"q": marker, "limit": 51},
            )
            assert too_many.status_code == 422, too_many.text
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)
