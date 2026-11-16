"""数据库变更脚本：新建规划执行表 plan_executions / plan_steps（2026-11-16）。

规划执行的数据面：一次计划一张 plan_executions 记录 + N 条 plan_steps 步骤行。
幂等：任一表已存在则跳过（两表同建同删）。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261116_plan_create.py
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
    from app.models.plan import PlanExecution, PlanStep

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            tables = inspector.get_table_names()
            if "plan_executions" in tables:
                print("plan_executions 表已存在，跳过")
                return
            # 用 ORM 模型的表定义建表，保证脚本与代码结构零漂移
            PlanExecution.__table__.create(bind=sync_conn, checkfirst=True)
            PlanStep.__table__.create(bind=sync_conn, checkfirst=True)
            print("已创建 plan_executions / plan_steps 表（规划执行）")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
