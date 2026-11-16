"""数据库变更脚本：新建并行排查表 investigation_dispatches / investigation_worker_runs（2026-11-16）。

并行子代理排查的数据面：一次排查一张 dispatch 记录 + N 条 worker 运行行。
幂等：任一表已存在则跳过（两表同建同删）。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261116_investigation_create.py
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
    from app.models.investigation import InvestigationDispatch, InvestigationWorkerRun

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            tables = inspector.get_table_names()
            if "investigation_dispatches" in tables:
                print("investigation_dispatches 表已存在，跳过")
                return
            # 用 ORM 模型的表定义建表，保证脚本与代码结构零漂移
            InvestigationDispatch.__table__.create(bind=sync_conn, checkfirst=True)
            InvestigationWorkerRun.__table__.create(bind=sync_conn, checkfirst=True)
            print("已创建 investigation_dispatches / investigation_worker_runs 表（并行排查）")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
