"""助理知识范围：检索在 SQL 层按目录/文档收口（真实嵌入 + 真实 PG）。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import login_access_token

# 两篇文档共用的检索词根：每次运行拼上随机后缀，避免历史库里的同名语料挤占 top-k
_SHARED_TERM = "星云共振校准"


async def _upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    path: str,
    title: str,
    content: str,
) -> dict:
    resp = await client.post(
        "/api/v1/knowledge/upload",
        headers=headers,
        files={"file": (f"{title}.txt", content.encode("utf-8"), "text/plain")},
        data={"path": path, "title": title},
    )
    if resp.status_code >= 500:
        pytest.skip(f"嵌入服务不可用: {resp.text}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _search(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    q: str = _SHARED_TERM,
    **params: object,
) -> list[str]:
    resp = await client.get(
        "/api/v1/knowledge/search",
        headers=headers,
        params={"q": q, "limit": 10, **params},
    )
    if resp.status_code >= 500:
        pytest.skip(f"检索不可用: {resp.text}")
    assert resp.status_code == 200, resp.text
    return [item["doc"]["id"] for item in resp.json()["items"]]


@pytest.mark.asyncio
async def test_search_scope_filters_by_folder_and_doc(
    env: dict[str, str], api_base: str
) -> None:
    """目录范围与本级/子级匹配；文档范围单独生效；两者并存取并集。"""

    suffix = uuid.uuid4().hex[:6]
    term = f"{_SHARED_TERM}{suffix}"
    folder_a = f"测试/范围甲-{suffix}"
    folder_b = f"测试/范围乙-{suffix}"
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        doc_a = await _upload(
            client,
            headers,
            path=folder_a,
            title=f"范围甲-{suffix}",
            content=f"{term}：甲目录的校准步骤。",
        )
        doc_b = await _upload(
            client,
            headers,
            path=folder_b,
            title=f"范围乙-{suffix}",
            content=f"{term}：乙目录的校准步骤。",
        )

        # 不设范围：两篇都命中（检索词带随机后缀，历史语料不会挤占 top-k）
        unscoped = await _search(client, headers, q=term)
        assert doc_a["id"] in unscoped
        assert doc_b["id"] in unscoped

        only_a = await _search(client, headers, q=term, scope_path=folder_a)
        assert doc_a["id"] in only_a
        assert doc_b["id"] not in only_a

        only_b = await _search(client, headers, q=term, scope_doc=[doc_b["id"]])
        assert doc_b["id"] in only_b
        assert doc_a["id"] not in only_b

        both = await _search(
            client, headers, q=term, scope_path=folder_a, scope_doc=[doc_b["id"]]
        )
        assert doc_a["id"] in both
        assert doc_b["id"] in both

        # 父目录命中子目录（本级或子级），不能误伤同前缀的兄弟目录
        parent = "测试"
        in_parent = await _search(client, headers, q=term, scope_path=parent)
        assert doc_a["id"] in in_parent
        assert doc_b["id"] in in_parent


@pytest.mark.asyncio
async def test_scope_preview_counts_and_unknown(
    env: dict[str, str], api_base: str
) -> None:
    """编辑器预览：命中数与不存在的目录/文档一次报全。"""

    suffix = uuid.uuid4().hex[:6]
    folder = f"测试/预览-{suffix}"
    missing = f"测试/不存在-{suffix}"
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        doc = await _upload(
            client,
            headers,
            path=folder,
            title=f"预览-{suffix}",
            content=f"{_SHARED_TERM}{suffix}：预览用文档。",
        )
        resp = await client.post(
            "/api/v1/knowledge/scope-preview",
            headers=headers,
            json={"paths": [folder, missing], "doc_ids": ["not-a-real-doc-id"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["doc_count"] >= 1
        assert any(row["path"] == folder and row["count"] >= 1 for row in body["matched_paths"])
        assert missing in body["unknown_paths"]
        assert "not-a-real-doc-id" in body["unknown_doc_ids"]

        exact = await client.post(
            "/api/v1/knowledge/scope-preview",
            headers=headers,
            json={"paths": [folder], "doc_ids": [doc["id"]]},
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["unknown_paths"] == []
        assert exact.json()["unknown_doc_ids"] == []
