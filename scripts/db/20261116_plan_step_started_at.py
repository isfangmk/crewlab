"""数据库变更脚本：plan_steps 增加 started_at 列（2026-11-16）。

步骤开始执行时刻：前端「已运行」读秒的锚点（刷新页面后仍可计时）。
幂等：列已存在则跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261116_plan_step_started_at.py
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
            columns = {c["name"] for c in inspector.get_columns("plan_steps")}
            if "started_at" in columns:
                print("plan_steps.started_at 已存在，跳过")
                return
            sync_conn.execute(
                sa.text("ALTER TABLE plan_steps ADD COLUMN started_at DATETIME")
            )
            print("已为 plan_steps 增加 started_at 列")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
