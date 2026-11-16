"""内置知识库文件同步：语料目录 → vault 文档，文件改动自动入库。

文件是唯一事实源（应用内 vault 文档只读）：
新增/修改 .md/.txt 即重切块重嵌入，删除文件即删文档。
文档身份 = 语料文件绝对路径（external_key），据此圈定归属与增量：
只同步、只清理本目录下的文档，不影响其他来源。

后台任务按固定间隔扫描，进程启动时立即同步一轮。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.knowledge import KnowledgeDoc
from app.services.knowledge.knowledge_service import delete_vault_doc, upsert_vault_doc

logger = logging.getLogger(__name__)

# 轮询间隔：文件改动秒级生效即可，过密徒增 IO
SYNC_INTERVAL_SECONDS = 5.0
# 刚落盘的文件（mtime 距今小于该秒数）跳过本轮，等写完整再同步
FRESH_FILE_GRACE_SECONDS = 2.0
VAULT_SUFFIXES = (".md", ".txt")


def _read_doc(path: Path) -> tuple[str, str]:
    """读取文件正文与标题：标题取首个一级标题，否则用文件名。"""

    text = path.read_text(encoding="utf-8-sig")
    title = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break
    return text, (title or path.stem)


def scan_vault_files(
    root: Path, *, fresh_grace_seconds: float = FRESH_FILE_GRACE_SECONDS
) -> dict[str, tuple[str, str, str]]:
    """扫描语料目录：返回 {文件绝对路径: (标题, 正文, 内容哈希)}。"""

    found: dict[str, tuple[str, str, str]] = {}
    if not root.is_dir():
        return found
    now_ts = time.time()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VAULT_SUFFIXES:
            continue
        if now_ts - path.stat().st_mtime < fresh_grace_seconds:
            continue  # 正在写入：等下一轮
        key = path.resolve().as_posix()
        try:
            text, title = _read_doc(path)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("内置知识库跳过不可读文件 %s: %s", key, exc)
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        found[key] = (title, text, digest)
    return found


async def sync_vault_once(
    db: AsyncSession, root: Path, *, fresh_grace_seconds: float = FRESH_FILE_GRACE_SECONDS
) -> dict[str, int]:
    """把语料目录同步进内置知识库一轮，返回 {created, updated, unchanged, deleted}。"""

    root = root.resolve()
    if not root.is_dir():
        # 语料目录不可见（目录迁移/挂载抖动）时整轮跳过：此时把「扫描不到文件」
        # 当成「文件已删除」会全量清掉内置知识库，代价远大于漏一轮同步。
        logger.warning("内置知识库目录不可见，跳过本轮同步: %s", root)
        return {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    prefix = root.as_posix().rstrip("/") + "/"
    files = scan_vault_files(root, fresh_grace_seconds=fresh_grace_seconds)
    stats = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    for key, (title, text, digest) in files.items():
        rel = key.removeprefix(prefix)
        rel_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        _, status = await upsert_vault_doc(
            db,
            external_key=key,
            external_hash=digest,
            title=title,
            content=text,
            path=rel_dir,
        )
        stats[status] += 1

    # 清理：本目录下、文件已消失的文档
    rows = await db.scalars(
        select(KnowledgeDoc.external_key).where(
            KnowledgeDoc.source_type == "vault",
            KnowledgeDoc.external_key.is_not(None),
        )
    )
    for key in rows.all():
        if not key or not key.startswith(prefix) or key in files:
            continue
        if await delete_vault_doc(db, external_key=key):
            stats["deleted"] += 1
    return stats


_task: asyncio.Task | None = None


async def _loop(root: Path) -> None:
    # 首轮即启动同步：首轮固定记日志，之后只在内容有变化时记
    first_round = True
    while True:
        try:
            from app.db import SessionLocal

            async with SessionLocal() as db:
                stats = await sync_vault_once(db, root)
            if first_round:
                logger.info(
                    "内置知识库启动同步 dir=%s created=%s updated=%s deleted=%s unchanged=%s",
                    root,
                    stats["created"],
                    stats["updated"],
                    stats["deleted"],
                    stats["unchanged"],
                )
            elif stats["created"] or stats["updated"] or stats["deleted"]:
                logger.info(
                    "内置知识库同步 created=%s updated=%s deleted=%s unchanged=%s",
                    stats["created"],
                    stats["updated"],
                    stats["deleted"],
                    stats["unchanged"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 同步失败不拖垮进程，下一轮重试
            logger.exception("内置知识库同步失败，下一轮重试")
        first_round = False
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


async def start_vault_sync() -> None:
    """起后台同步循环（首轮随任务跑，不阻塞启动）；语料目录不存在则创建。"""

    global _task
    root = Path(get_settings().knowledge_vault_dir)
    root.mkdir(parents=True, exist_ok=True)
    _task = asyncio.create_task(_loop(root))


async def stop_vault_sync() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
