"""表结构变更：task_graph_nodes 增加 agent_id 列（节点复用指定助理）。

仅加列，不涉及业务数据修复；历史节点该列为空，行为不变。重复执行安全。

用法：.venv/Scripts/python.exe scripts/db/20261119_task_graph_nodes_add_agent_id.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import os  # noqa: E402

import asyncpg  # noqa: E402


async def main() -> None:
    url = os.environ["DATABASE_URL"]
    if not url.startswith("postgresql"):
        raise SystemExit(f"本脚本仅支持 PostgreSQL，当前: {url.split('://')[0]}")
    # asyncpg 不吃 SQLAlchemy 的 driver 前缀
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='task_graph_nodes' AND column_name='agent_id'"
        )
        if exists:
            print("列已存在: task_graph_nodes.agent_id")
        else:
            await conn.execute(
                "ALTER TABLE task_graph_nodes "
                "ADD COLUMN agent_id VARCHAR(64) NOT NULL DEFAULT ''"
            )
            print("已加列: task_graph_nodes.agent_id")
    finally:
        await conn.close()
    print("完成")


asyncio.run(main())
