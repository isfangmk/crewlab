"""外部知识源：URL / Git 接入、增量同步、只读约束与删除清理。

测试打真实后端（启动中的服务）与真实依赖：本地 HTTP 服务、临时 Git 仓库、
真实切块与嵌入。所有创建的来源都在 finally 中删除，文档与向量随之清理。
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from tests.conftest import login_access_token

SOURCES_URL = "/api/v1/knowledge/sources"
DOCS_URL = "/api/v1/knowledge/docs"


async def _headers(client: httpx.AsyncClient, env: dict[str, str]) -> dict[str, str]:
    if (await client.get("/healthz")).status_code != 200:
        pytest.skip("后端未就绪")
    token = await login_access_token(client, env)
    return {"Authorization": f"Bearer {token}"}


async def _create_source(
    client: httpx.AsyncClient, headers: dict[str, str], **body: object
) -> dict:
    resp = await client.post(SOURCES_URL, headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _wait_sync(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    source_id: str,
    marker: str,
    *,
    timeout: float = 120.0,
) -> dict:
    """轮询来源列表直到后台同步结束；返回最后一行的 wire 数据。"""

    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        resp = await client.get(SOURCES_URL, headers=headers, params={"q": marker})
        assert resp.status_code == 200, resp.text
        row = next(
            (item for item in resp.json()["items"] if item["id"] == source_id), None
        )
        assert row is not None, "来源在列表中消失"
        last = row
        if row["sync_status"] in ("ok", "error"):
            return row
        await asyncio.sleep(0.5)
    raise AssertionError(f"同步超时: {last}")


async def _sync(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    source_id: str,
    marker: str,
) -> dict:
    resp = await client.post(f"{SOURCES_URL}/{source_id}/sync", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sync_status"] == "syncing"
    row = await _wait_sync(client, headers, source_id, marker)
    assert row["sync_status"] == "ok", row["last_error"]
    return row


async def _docs_of(
    client: httpx.AsyncClient, headers: dict[str, str], source_id: str
) -> list[dict]:
    resp = await client.get(
        DOCS_URL, headers=headers, params={"external_source_id": source_id, "limit": 100}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _doc_server() -> Iterator[tuple[str, dict]]:
    state: dict = {
        "content": "# 部署说明\n\n第一版正文。",
        "etag": '"v1"',
        "requests": 0,
        "not_modified": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["requests"] += 1
            if self.headers.get("If-None-Match") == state["etag"]:
                state["not_modified"] += 1
                self.send_response(304)
                self.end_headers()
                return
            body = state["content"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("ETag", state["etag"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/doc.md", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def doc_server() -> Iterator[tuple[str, dict]]:
    yield from _doc_server()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _init_repo(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "config", "user.email", "knowledge-test@example.com")
    _git(repo, "config", "user.name", "Knowledge Test")


@pytest.mark.asyncio
async def test_url_source_sync_incremental_and_cleanup(
    env: dict[str, str], api_base: str, doc_server: tuple[str, dict]
) -> None:
    """URL 来源：抓取入库、条件请求 304、内容变更刷新、来源删除清空文档。"""

    location, state = doc_server
    marker = f"URL源{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=180.0) as client:
        headers = await _headers(client, env)
        source = await _create_source(
            client,
            headers,
            name=marker,
            kind="url",
            location=location,
            knowledge_path=f"外部源/{marker}",
        )
        source_id = source["id"]
        try:
            row = await _sync(client, headers, source_id, marker)
            assert row["document_count"] == 1
            assert row["last_synced_at"] is not None

            docs = await _docs_of(client, headers, source_id)
            assert len(docs) == 1
            doc = docs[0]
            assert doc["source_type"] == "url"
            assert doc["external_source_id"] == source_id
            assert doc["external_key"] == location
            assert doc["path"] == f"外部源/{marker}"
            assert doc["chunk_count"] >= 1
            assert doc["title"] == "部署说明"

            # 外部源文档随同步刷新，禁止直接修改
            rejected = await client.patch(
                f"{DOCS_URL}/{doc['id']}",
                headers=headers,
                json={"title": "改名", "content": "改正文"},
            )
            assert rejected.status_code == 403, rejected.text

            # 内容未变时走 If-None-Match → 304，不重复入库
            before_requests = state["requests"]
            row = await _sync(client, headers, source_id, marker)
            assert row["document_count"] == 1
            assert state["requests"] > before_requests
            assert state["not_modified"] >= 1

            # 内容更新后哈希变化，正文与标题刷新
            state["content"] = "# 部署说明\n\n第二版正文，新增回滚步骤。"
            state["etag"] = '"v2"'
            await _sync(client, headers, source_id, marker)
            detail = await client.get(f"{DOCS_URL}/{doc['id']}", headers=headers)
            assert detail.status_code == 200, detail.text
            assert "第二版正文" in detail.json()["content"]

            deleted = await client.delete(f"{SOURCES_URL}/{source_id}", headers=headers)
            assert deleted.status_code == 200, deleted.text
            assert await _docs_of(client, headers, source_id) == []
        finally:
            await client.delete(f"{SOURCES_URL}/{source_id}", headers=headers)


@pytest.mark.asyncio
async def test_git_source_sync_path_prefix_and_stale_removal(
    env: dict[str, str], api_base: str, tmp_path: Path
) -> None:
    """Git 来源：只同步目录前缀内文档，二次同步更新改动并移除已删文件。"""

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    docs_dir = repo / "docs"
    docs_dir.mkdir()
    (docs_dir / "alpha.md").write_text("# Alpha\n\n第一版内容。", encoding="utf-8")
    (docs_dir / "beta.md").write_text("# Beta\n\n待删除内容。", encoding="utf-8")
    (repo / "root.md").write_text("# Root\n\n前缀外内容。", encoding="utf-8")
    _commit(repo, "init docs")

    marker = f"Git源{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=180.0) as client:
        headers = await _headers(client, env)
        source = await _create_source(
            client,
            headers,
            name=marker,
            kind="git",
            location=repo.as_uri(),
            branch="main",
            path_prefix="docs",
            knowledge_path=f"外部源/{marker}",
        )
        source_id = source["id"]
        try:
            row = await _sync(client, headers, source_id, marker)
            assert row["document_count"] == 2
            docs = await _docs_of(client, headers, source_id)
            assert {d["external_key"] for d in docs} == {"docs/alpha.md", "docs/beta.md"}
            assert all(d["source_type"] == "git" for d in docs)
            assert all(d["path"] == f"外部源/{marker}" for d in docs)
            alpha = next(d for d in docs if d["external_key"] == "docs/alpha.md")
            assert alpha["title"] == "Alpha"

            # 修改一份、删除一份、新增一份，再同步
            (docs_dir / "alpha.md").write_text(
                "# Alpha\n\n第二版内容，补充告警阈值。", encoding="utf-8"
            )
            (docs_dir / "beta.md").unlink()
            (docs_dir / "gamma.md").write_text("# Gamma\n\n新增文档。", encoding="utf-8")
            _commit(repo, "update docs")

            row = await _sync(client, headers, source_id, marker)
            assert row["document_count"] == 2
            docs = await _docs_of(client, headers, source_id)
            assert {d["external_key"] for d in docs} == {
                "docs/alpha.md",
                "docs/gamma.md",
            }
            alpha = next(d for d in docs if d["external_key"] == "docs/alpha.md")
            detail = await client.get(f"{DOCS_URL}/{alpha['id']}", headers=headers)
            assert detail.status_code == 200, detail.text
            assert "第二版内容" in detail.json()["content"]

            # 来源停用立即作用于已同步文档；重新启用同步恢复
            disabled = await client.patch(
                f"{SOURCES_URL}/{source_id}", headers=headers, json={"enabled": False}
            )
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["enabled"] is False
            assert all(not d["enabled"] for d in await _docs_of(client, headers, source_id))

            enabled = await client.patch(
                f"{SOURCES_URL}/{source_id}", headers=headers, json={"enabled": True}
            )
            assert enabled.status_code == 200, enabled.text
            assert all(d["enabled"] for d in await _docs_of(client, headers, source_id))
        finally:
            await client.delete(f"{SOURCES_URL}/{source_id}", headers=headers)


@pytest.mark.asyncio
async def test_url_source_sync_failure_records_error(
    env: dict[str, str], api_base: str
) -> None:
    """抓取失败要落到 error 状态与可读原因，来源仍可删除重试。"""

    marker = f"失败源{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        headers = await _headers(client, env)
        source = await _create_source(
            client,
            headers,
            name=marker,
            kind="url",
            location="http://127.0.0.1:9/unreachable.md",
        )
        source_id = source["id"]
        try:
            resp = await client.post(f"{SOURCES_URL}/{source_id}/sync", headers=headers)
            assert resp.status_code == 200, resp.text
            row = await _wait_sync(client, headers, source_id, marker)
            assert row["sync_status"] == "error"
            assert "抓取 URL 失败" in row["last_error"]
            assert row["document_count"] == 0
        finally:
            await client.delete(f"{SOURCES_URL}/{source_id}", headers=headers)


@pytest.mark.asyncio
async def test_source_validation_list_filters_and_delete_cleanup(
    env: dict[str, str], api_base: str, doc_server: tuple[str, dict]
) -> None:
    """来源校验、列表筛选、类型不可变与删除后文档清空。"""

    location, _state = doc_server
    marker = f"校验源{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        headers = await _headers(client, env)

        invalid = await client.post(
            SOURCES_URL,
            headers=headers,
            json={"name": f"{marker}非法", "kind": "url", "location": "ftp://example.com/a"},
        )
        assert invalid.status_code == 400, invalid.text

        first = await _create_source(
            client,
            headers,
            name=marker,
            kind="url",
            location=location,
        )
        second = await _create_source(
            client,
            headers,
            name=f"{marker}备份",
            kind="git",
            location="https://git.example.com/ops/repo.git",
        )
        try:
            duplicate = await client.post(
                SOURCES_URL,
                headers=headers,
                json={"name": marker, "kind": "url", "location": location},
            )
            assert duplicate.status_code == 400, duplicate.text

            changed_kind = await client.patch(
                f"{SOURCES_URL}/{first['id']}", headers=headers, json={"kind": "git"}
            )
            assert changed_kind.status_code == 400, changed_kind.text

            url_only = await client.get(
                SOURCES_URL, headers=headers, params={"q": marker, "kind": "url"}
            )
            assert url_only.status_code == 200, url_only.text
            ids = {item["id"] for item in url_only.json()["items"]}
            assert ids == {first["id"]}

            hit = await client.get(SOURCES_URL, headers=headers, params={"q": f"{marker}备份"})
            assert hit.status_code == 200, hit.text
            assert [item["id"] for item in hit.json()["items"]] == [second["id"]]

            await _sync(client, headers, first["id"], marker)
            assert len(await _docs_of(client, headers, first["id"])) == 1

            deleted = await client.delete(f"{SOURCES_URL}/{first['id']}", headers=headers)
            assert deleted.status_code == 200, deleted.text
            assert await _docs_of(client, headers, first["id"]) == []
            remaining = await client.get(SOURCES_URL, headers=headers, params={"q": marker})
            assert remaining.status_code == 200, remaining.text
            assert [item["id"] for item in remaining.json()["items"]] == [second["id"]]
        finally:
            await client.delete(f"{SOURCES_URL}/{first['id']}", headers=headers)
            await client.delete(f"{SOURCES_URL}/{second['id']}", headers=headers)
