"""内置知识库文件同步：文件为准的增改删与哈希跳过（真库真嵌入，自清理）。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_orm.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services.knowledge_vault_sync import sync_vault_once

pytestmark = pytest.mark.asyncio


async def _vault_rows(root: Path) -> list[KnowledgeDoc]:
    """取本测试语料目录下的 vault 文档。"""

    prefix = root.resolve().as_posix() + "/"
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(KnowledgeDoc).where(
                KnowledgeDoc.source_type == "vault",
                KnowledgeDoc.external_key.like(prefix + "%"),
            )
        )
        return list(rows.all())


async def _chunk_count(doc_id: str) -> int:
    async with SessionLocal() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.doc_id == doc_id
                )
            )
        )


@pytest.fixture
def vault_root(tmp_path: Path):
    """独占的临时语料目录；用完连同其 vault 文档一并清掉。"""

    root = tmp_path / f"vault-{uuid.uuid4().hex[:8]}"
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)

    async def _cleanup() -> None:
        async with SessionLocal() as db:
            await sync_vault_once(db, root, fresh_grace_seconds=0)

    import asyncio

    asyncio.run(_cleanup())


async def test_file_lifecycle_create_update_delete(vault_root: Path) -> None:
    sub = vault_root / "诊断" / "dns"
    sub.mkdir(parents=True)
    file = sub / "排查手册.md"
    file.write_text("# 排查手册\n\n第一版内容。", encoding="utf-8")

    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    assert stats["created"] == 1
    rows = await _vault_rows(vault_root)
    assert len(rows) == 1
    doc = rows[0]
    assert doc.title == "排查手册"
    assert doc.path == "诊断/dns"
    assert doc.external_key == file.resolve().as_posix()
    assert doc.content.startswith("# 排查手册")
    assert await _chunk_count(doc.id) > 0

    # 未变化：哈希跳过，不重复入库
    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    assert stats == {"created": 0, "updated": 0, "unchanged": 1, "deleted": 0}

    # 修改文件：内容更新并重切块
    file.write_text("# 排查手册\n\n第二版内容，补充了 dig 用法。", encoding="utf-8")
    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    assert stats["updated"] == 1
    rows = await _vault_rows(vault_root)
    assert "第二版" in rows[0].content

    # 删除文件：文档与切块一并清掉
    file.unlink()
    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    assert stats["deleted"] == 1
    assert await _vault_rows(vault_root) == []


async def test_root_file_path_and_title_fallback(vault_root: Path) -> None:
    (vault_root / "readme.txt").write_text("没有一级标题的正文。", encoding="utf-8")

    async with SessionLocal() as db:
        await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    rows = await _vault_rows(vault_root)
    assert rows[0].title == "readme"
    assert rows[0].path == ""


async def test_move_file_updates_path(vault_root: Path) -> None:
    old = vault_root / "旧目录"
    old.mkdir()
    file = old / "文档.md"
    file.write_text("# 文档\n\n内容。", encoding="utf-8")
    async with SessionLocal() as db:
        await sync_vault_once(db, vault_root, fresh_grace_seconds=0)

    new = vault_root / "新目录"
    new.mkdir()
    file.rename(new / "文档.md")
    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    rows = await _vault_rows(vault_root)
    assert stats["created"] == 1 and stats["deleted"] == 1
    assert rows[0].path == "新目录"


async def test_non_text_files_ignored(vault_root: Path) -> None:
    (vault_root / "图片.png").write_bytes(b"\x89PNG fake")
    (vault_root / "笔记.md").write_text("# 笔记\n\n正文。", encoding="utf-8")

    async with SessionLocal() as db:
        stats = await sync_vault_once(db, vault_root, fresh_grace_seconds=0)
    assert stats["created"] == 1
    rows = await _vault_rows(vault_root)
    assert [r.title for r in rows] == ["笔记"]
