"""数据库变更脚本：vault 文档 external_key 随语料目录迁移改基（2026-11-15）。

内置知识库语料从仓库根「内置知识库/」迁入「backend/app/knowledge_base/」，
vault 同步的文档身份键 external_key 是文件绝对路径，不改基会导致旧文档孤儿、
新路径重复入库。本脚本把旧前缀整体改写为新前缀，文档 id/切块/向量不动。
幂等：已是新前缀或不属旧前缀的行跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_vault_external_key_rebase.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# DATABASE_URL 是相对路径（./data/...），必须以 backend/ 为工作目录，和后端进程一致
from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / "backend" / ".env")
os.chdir(_REPO_ROOT / "backend")
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import sqlalchemy as sa

_OLD_PREFIX = (_REPO_ROOT / "内置知识库").resolve().as_posix().rstrip("/") + "/"
_NEW_PREFIX = (_REPO_ROOT / "backend" / "app" / "knowledge_base").resolve().as_posix().rstrip("/") + "/"


async def main() -> None:
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "knowledge_docs" not in inspector.get_table_names():
                print("knowledge_docs 表不存在，跳过")
                return
            rows = sync_conn.execute(
                sa.text(
                    "SELECT id, external_key FROM knowledge_docs "
                    "WHERE source_type='vault' AND external_key LIKE :old"
                ),
                {"old": _OLD_PREFIX + "%"},
            ).fetchall()
            if not rows:
                print("无旧前缀 vault 文档，跳过")
                return
            for doc_id, key in rows:
                new_key = _NEW_PREFIX + key[len(_OLD_PREFIX):]
                sync_conn.execute(
                    sa.text("UPDATE knowledge_docs SET external_key=:k WHERE id=:id"),
                    {"k": new_key, "id": doc_id},
                )
            print(f"external_key 改基 {len(rows)} 篇：")
            print(f"  {_OLD_PREFIX}\n  -> {_NEW_PREFIX}")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
