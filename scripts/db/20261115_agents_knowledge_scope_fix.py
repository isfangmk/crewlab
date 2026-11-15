"""数据库变更脚本：助理知识范围挂到真实知识库目录（2026-11-15）。

4 个运维助理的 knowledge_scope_json 指向「运维文档」，而知识库经 vault 同步
重建后真实目录是 diagnostics/systems/时区/concepts/reference，原指向命中 0 篇。
统一改挂真实目录（「测试/」是残留测试文档，不纳入）。
幂等：不再含「运维文档」的行跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_agents_knowledge_scope_fix.py
"""

from __future__ import annotations

import asyncio
import json
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

# 真实知识库目录（内置知识库 vault 同步产物，不含「测试/」残留）
REAL_PATHS = ["diagnostics", "systems", "时区", "concepts", "reference"]


async def main() -> None:
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "user_agents" not in inspector.get_table_names():
                print("user_agents 表不存在，跳过")
                return
            rows = sync_conn.execute(
                sa.text(
                    "SELECT name, knowledge_scope_json FROM user_agents "
                    "WHERE knowledge_scope_json LIKE '%运维文档%'"
                )
            ).fetchall()
            if not rows:
                print("无引用「运维文档」的助理，跳过")
                return
            new_scope = json.dumps({"paths": REAL_PATHS, "doc_ids": []}, ensure_ascii=False)
            for name, _old in rows:
                sync_conn.execute(
                    sa.text(
                        "UPDATE user_agents SET knowledge_scope_json=:scope WHERE name=:name"
                    ),
                    {"scope": new_scope, "name": name},
                )
                print(f"{name}: 运维文档 → {'/'.join(REAL_PATHS)}")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
