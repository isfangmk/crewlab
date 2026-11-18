"""数据库变更脚本：observation_tool_calls 增加工具调用字符数统计列（2026-11-18）。

为排查上下文 token 膨胀，记录每次工具/技能调用占用的字符数：
- input_chars  = 入参 JSON 字符数（tool_start 时记）
- result_chars = 回喂模型的工具结果字符数（tool_end 时记）

两列均 NOT NULL DEFAULT 0，存量行补 0（历史数据无字符统计，不造假）。
幂等：列已存在则跳过，可重复执行。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261118_tool_call_chars.py
"""

from __future__ import annotations

import asyncio
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
    from app.db import engine

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> None:
            inspector = sa.inspect(sync_conn)
            if "observation_tool_calls" not in inspector.get_table_names():
                print("observation_tool_calls 表不存在，跳过")
                return
            existing = {c["name"] for c in inspector.get_columns("observation_tool_calls")}
            for name in ("input_chars", "result_chars"):
                if name in existing:
                    print(f"observation_tool_calls.{name} 已存在，跳过")
                    continue
                sync_conn.execute(
                    sa.text(
                        f"ALTER TABLE observation_tool_calls ADD COLUMN {name} "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                )
                print(f"已添加 observation_tool_calls.{name}")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
