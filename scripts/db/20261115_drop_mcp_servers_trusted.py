"""数据库变更脚本：mcp_servers 删除 trusted 列（2026-11-15）。

Trusted 开关已砍，免审统一由 approval_policy='never' 表达；
先把存量 trusted 行的策略修正为 never（保行为），再删列。
幂等：表或列不存在时直接跳过，可重复执行。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_drop_mcp_servers_trusted.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 脚本独立运行：先载入 backend/.env 再导入 app.*；
# DATABASE_URL 是相对路径（./data/...），必须以 backend/ 为工作目录，和后端进程一致
import os

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / "backend" / ".env")
os.chdir(_REPO_ROOT / "backend")
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import sqlalchemy as sa


async def main() -> None:
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "mcp_servers" not in inspector.get_table_names():
                print("mcp_servers 表不存在，跳过")
                return
            columns = {c["name"] for c in inspector.get_columns("mcp_servers")}
            if "trusted" not in columns:
                print("mcp_servers.trusted 不存在，跳过")
                return
            sync_conn.execute(
                sa.text("UPDATE mcp_servers SET approval_policy = 'never' WHERE trusted")
            )
            sync_conn.execute(sa.text("ALTER TABLE mcp_servers DROP COLUMN trusted"))
            print("已修正存量策略并删除 mcp_servers.trusted")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
