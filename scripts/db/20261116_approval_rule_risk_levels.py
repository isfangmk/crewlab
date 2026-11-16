"""数据库变更脚本：approval_rules.risk 旧值 approval_required 改写为 dangerous（2026-11-16）。

工具风险统一为三级危险度（safe / mutating / dangerous），与审批语义拆轴：
旧值 approval_required 语义是「只对高危工具生效的规则」，等价映射到 dangerous。
幂等：无旧值行时 UPDATE 影响 0 行，重复跑无副作用。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261116_approval_rule_risk_levels.py
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
            if "approval_rules" not in inspector.get_table_names():
                print("approval_rules 表不存在，跳过")
                return
            result = sync_conn.execute(
                sa.text(
                    "UPDATE approval_rules SET risk = 'dangerous' WHERE risk = 'approval_required'"
                )
            )
            print(f"approval_rules.risk 旧值改写为 dangerous：{result.rowcount} 行")
            rows = sync_conn.execute(
                sa.text("SELECT DISTINCT risk FROM approval_rules")
            ).all()
            print(f"当前取值集合：{[r[0] for r in rows]}")

        await conn.run_sync(_migrate)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
