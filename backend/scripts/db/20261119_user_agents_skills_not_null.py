"""表结构变更：user_agents.skills_json 不再用 NULL 表示继承全部技能。

NULL 统一回填 '[]'（不装配任何技能），再设 NOT NULL 与默认值 '[]'。
重复执行安全。

用法：.venv/Scripts/python.exe scripts/db/20261119_user_agents_skills_not_null.py
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
        await conn.execute("UPDATE user_agents SET skills_json = '[]' WHERE skills_json IS NULL")
        await conn.execute(
            "ALTER TABLE user_agents ALTER COLUMN skills_json SET DEFAULT '[]'"
        )
        await conn.execute(
            "ALTER TABLE user_agents ALTER COLUMN skills_json SET NOT NULL"
        )
        print("已完成: skills_json NULL 回填 '[]' 并设为 NOT NULL DEFAULT '[]'")
    finally:
        await conn.close()
    print("完成")


asyncio.run(main())
