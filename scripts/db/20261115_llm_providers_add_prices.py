"""数据库变更脚本：llm_providers 增加手动单价表列（2026-11-15）。

用量与成本看板的估算成本只认手动单价（不读 litellm 价目表）：
model_prices_json 存 {"<模型名>": {"input_per_1m": 3.5, "output_per_1m": 7.0}}，
每百万 token 单价；缺价目的模型成本显示「未计价」，不造假 0。
幂等：列已存在则跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_llm_providers_add_prices.py
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
            if "llm_providers" not in inspector.get_table_names():
                print("llm_providers 表不存在，跳过")
                return
            columns = {c["name"] for c in inspector.get_columns("llm_providers")}
            if "model_prices_json" in columns:
                print("model_prices_json 列已存在，跳过")
                return
            sync_conn.execute(
                sa.text(
                    "ALTER TABLE llm_providers ADD COLUMN model_prices_json TEXT NULL"
                )
            )
            print("llm_providers 已添加 model_prices_json 列（手动单价表，NULL = 未定价）")

        await conn.run_sync(_migrate)


if __name__ == "__main__":
    asyncio.run(main())
