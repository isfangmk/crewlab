"""数据库变更脚本：chat_messages 新增 stats_json 列（2026-11-15）。

回答统计（耗时 / token）随每条 assistant 气泡持久化，列存 JSON：
{"duration_ms", "prompt_tokens", "completion_tokens", "total_tokens"}，仅非空键。
幂等：列已存在时直接跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_chat_messages_add_stats_json.py
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
            if "chat_messages" not in inspector.get_table_names():
                print("chat_messages 表不存在，跳过")
                return
            columns = {c["name"] for c in inspector.get_columns("chat_messages")}
            if "stats_json" in columns:
                print("chat_messages.stats_json 已存在，跳过")
                return
            sync_conn.execute(sa.text("ALTER TABLE chat_messages ADD COLUMN stats_json TEXT"))
            print("已新增 chat_messages.stats_json")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
