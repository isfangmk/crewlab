"""数据库变更脚本：chat_sessions 新增 thinking_enabled / web_search_enabled 列（2026-11-16）。

「思考 / 联网搜索」开关随会话持久化（每个会话各自记住），换页面/换设备打开即同步。
旧行补默认关（FALSE），不造假历史状态。
幂等：列已存在时直接跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261116_chat_session_prefs.py
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


async def main() -> None:
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "chat_sessions" not in inspector.get_table_names():
                print("chat_sessions 表不存在，跳过")
                return
            columns = {c["name"] for c in inspector.get_columns("chat_sessions")}
            for column in ("thinking_enabled", "web_search_enabled"):
                if column in columns:
                    print(f"chat_sessions.{column} 已存在，跳过")
                    continue
                sync_conn.execute(
                    sa.text(
                        f"ALTER TABLE chat_sessions ADD COLUMN {column} "
                        "BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
                print(f"已新增 chat_sessions.{column}")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
