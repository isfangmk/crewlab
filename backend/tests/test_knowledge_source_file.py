"""知识库上传原件：落盘/路径白名单/删除清理（真实文件系统）+ 下载接口集成。"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.services.knowledge import knowledge_service
from tests.conftest import login_access_token


def test_source_file_roundtrip_and_whitelist(monkeypatch, tmp_path) -> None:
    """真实文件系统上验证保存/路径/删除；扩展名白名单拒绝任意类型。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        knowledge_service.save_source_file(doc_id="doc-1", filename="巡检手册.md", raw=b"# hi")
        path = knowledge_service.source_file_path("doc-1", "巡检手册.md")
        # 原件按 {文档 id}{原扩展名} 落盘，文件名本身不参与路径拼接（无穿越面）
        assert path.is_file()
        assert path.name == "doc-1.md"
        assert path.read_bytes() == b"# hi"

        # 删除清理；重复删除（文件已不存在）同样成功
        knowledge_service.delete_source_file(doc_id="doc-1", filename="巡检手册.md")
        assert not path.exists()
        knowledge_service.delete_source_file(doc_id="doc-1", filename="巡检手册.md")

        # 白名单外 / 无扩展名显式失败
        with pytest.raises(ValueError, match="扩展名不受支持"):
            knowledge_service.source_file_path("doc-1", "evil.exe")
        with pytest.raises(ValueError, match="扩展名不受支持"):
            knowledge_service.source_file_path("doc-1", "no-extension")
    finally:
        # monkeypatch 恢复环境变量后清缓存，避免污染后续用例
        get_settings.cache_clear()


def _backend_upload_dir() -> Path:
    """推算真后端的原件存储目录（与 backend/.env 的运行口径一致）。"""

    root = Path(__file__).resolve().parents[1]
    configured = ""
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("KNOWLEDGE_UPLOAD_DIR="):
                configured = line.split("=", 1)[1].strip().strip('"')
    if not configured:
        configured = "./data/uploads"
    path = Path(configured)
    return path if path.is_absolute() else (root / path).resolve()


async def test_upload_saves_source_and_delete_cleans_it(
    env: dict[str, str], api_base: str
) -> None:
    """真实后端集成：上传落盘 → 下载字节一致 → 删除文档 → 原件被清理。

    打真实嵌入服务，消耗一次小文档嵌入（约 200 token）。
    """

    storage = _backend_upload_dir()
    async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        files = {"file": ("落盘验证.txt", "落盘验证正文：源文件随上传保存。".encode("utf-8"), "text/plain")}
        up = await client.post(
            "/api/v1/knowledge/upload",
            headers=headers,
            files=files,
            data={"path": "落盘验证", "title": "落盘验证"},
        )
        if up.status_code >= 500:
            pytest.skip(f"嵌入服务不可用: {up.text}")
        assert up.status_code == 200, up.text
        doc = up.json()

        try:
            # 上传后原件真实存在于后端存储目录
            stored = storage / (doc["id"] + ".txt")
            assert stored.is_file(), f"原件未落盘: {stored}"
            assert "源文件随上传保存" in stored.read_text(encoding="utf-8")

            # 下载接口字节一致，文件名（RFC5987 解码后）来自原件名
            dl = await client.get(f"/api/v1/knowledge/docs/{doc['id']}/source", headers=headers)
            assert dl.status_code == 200, dl.text
            assert dl.content == stored.read_bytes()
            disposition = urllib.parse.unquote(dl.headers.get("content-disposition", ""))
            assert "落盘验证.txt" in disposition
        finally:
            await client.delete(f"/api/v1/knowledge/docs/{doc['id']}", headers=headers)

        # 删除文档后原件同步清理
        assert not stored.exists()


async def test_download_source_404_for_missing_doc(env: dict[str, str], api_base: str) -> None:
    """下载接口对不存在的文档显式 404，不做静默降级。"""

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/knowledge/docs/not-exist-doc/source", headers=headers)
        assert resp.status_code == 404
