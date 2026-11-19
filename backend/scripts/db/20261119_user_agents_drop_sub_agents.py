"""表结构变更：删除 user_agents.sub_agents_json 列（委托机制已移除）。

仅删列，不涉及业务数据修复：委托数据随列一起丢弃，不再使用。重复执行安全。

用法：.venv/Scripts/python.exe scripts/db/20261119_user_agents_drop_sub_agents.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402


async def main() -> None:
    url = os.environ["DATABASE_URL"]
    if not url.startswith("postgresql"):
        raise SystemExit(f"本脚本仅支持 PostgreSQL，当前: {url.split('://')[0]}")
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='user_agents' AND column_name='sub_agents_json'"
        )
        if exists:
            await conn.execute(
                "ALTER TABLE user_agents DROP COLUMN sub_agents_json"
            )
            print("已删列: user_agents.sub_agents_json")
        else:
            print("列不存在，无需删除: user_agents.sub_agents_json")
    finally:
        await conn.close()
    print("完成")


asyncio.run(main())
