"""数据库变更脚本：user_agents 增加内置工具勾选列（2026-11-19）。

内置工具由「全部常驻」改为「按助理勾选装配」，新增 builtin_tools_json 列：
NOT NULL DEFAULT '[]'，存量行回填当前全部可勾选工具名（保持其原有全量装配行为，
不篡改用户配置）。

幂等：列已存在则跳过，可重复执行。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261119_user_agents_add_builtin_tools.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / "backend" / ".env")
os.chdir(_REPO_ROOT / "backend")
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import sqlalchemy as sa


async def main() -> None:
    from app.tools.catalog import builtin_selectable_tool_names

    full_list = json.dumps(builtin_selectable_tool_names(), ensure_ascii=False)
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "user_agents" not in inspector.get_table_names():
                print("user_agents 表不存在，跳过")
                return
            existing = {c["name"] for c in inspector.get_columns("user_agents")}
            if "builtin_tools_json" in existing:
                print("user_agents.builtin_tools_json 已存在，跳过")
                return
            # 先加可空列，回填全量名单后再收紧为 NOT NULL
            sync_conn.execute(
                sa.text(
                    "ALTER TABLE user_agents ADD COLUMN builtin_tools_json TEXT"
                )
            )
            sync_conn.execute(
                sa.text(
                    "UPDATE user_agents SET builtin_tools_json = :full "
                    "WHERE builtin_tools_json IS NULL"
                ),
                {"full": full_list},
            )
            sync_conn.execute(
                sa.text(
                    "ALTER TABLE user_agents ALTER COLUMN builtin_tools_json "
                    "SET DEFAULT '[]'"
                )
            )
            sync_conn.execute(
                sa.text(
                    "ALTER TABLE user_agents ALTER COLUMN builtin_tools_json SET NOT NULL"
                )
            )
            print("已添加 user_agents.builtin_tools_json，存量行回填全部内置工具")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
