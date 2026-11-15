"""数据库变更脚本：新建审批规则表 approval_rules（2026-11-15）。

审批规则引擎的规则存储：按 工具名通配 × 助理 × 风险 匹配，
动作四档 auto_approve / require_approval / block / escalate，
优先级确定（具体名 > 前缀通配 > 通配；助理专属 > 全局；priority 降序）。
幂等：表已存在则跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_approval_rules_create.py
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
    from app.models.approval_rule import ApprovalRule

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "approval_rules" in inspector.get_table_names():
                print("approval_rules 表已存在，跳过")
                return
            # 用 ORM 模型的表定义建表，保证脚本与代码结构零漂移
            ApprovalRule.__table__.create(bind=sync_conn, checkfirst=True)
            print("已创建 approval_rules 表（审批规则：工具×助理×风险）")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
