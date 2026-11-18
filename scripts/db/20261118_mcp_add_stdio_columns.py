"""数据库变更脚本：mcp_servers 增加 stdio 传输所需列（2026-11-18）。

为把 codebase-memory-mcp（仅 stdio）接入 crewlab MCP 体系，mcp_servers
新增 command / args_json / cwd / env_json 四列。已有行保持原样（http/sse
不需这些列），新列均有默认值。

幂等：列已存在则跳过，可重复执行。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261118_mcp_add_stdio_columns.py
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

# 列名 -> SQL 列定义（带默认值，避免存量行的 NOT NULL 冲突）
_NEW_COLUMNS = {
    "command": "VARCHAR(1024) NOT NULL DEFAULT ''",
    "args_json": "TEXT NOT NULL DEFAULT '[]'",
    "cwd": "VARCHAR(1024) NOT NULL DEFAULT ''",
    "env_json": "TEXT NOT NULL DEFAULT '{}'",
}


async def main() -> None:
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "mcp_servers" not in inspector.get_table_names():
                print("mcp_servers 表不存在，跳过")
                return
            existing = {c["name"] for c in inspector.get_columns("mcp_servers")}
            for name, definition in _NEW_COLUMNS.items():
                if name in existing:
                    print(f"mcp_servers.{name} 已存在，跳过")
                    continue
                sync_conn.execute(
                    sa.text(f"ALTER TABLE mcp_servers ADD COLUMN {name} {definition}")
                )
                print(f"已添加 mcp_servers.{name}")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
