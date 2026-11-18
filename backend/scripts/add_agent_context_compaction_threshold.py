"""一次性加列：user_agents 增加 context_compaction_threshold（上下文压缩触发 token 阈值）。

代码模型已加字段；SQLite 的 create_all 不会给已存在的表补列，历史库需手动补列。
NULL = 继承全局默认，正好符合"缺列历史行继承默认"的语义。
重复执行安全：列已存在则跳过。
用法：.venv/Scripts/python.exe scripts/add_agent_context_compaction_threshold.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"


def main() -> None:
    db = sqlite3.connect(DB)
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(user_agents)")}
        if "context_compaction_threshold" in cols:
            print("context_compaction_threshold 列已存在，无需处理")
            return
        db.execute(
            "ALTER TABLE user_agents ADD COLUMN context_compaction_threshold INTEGER DEFAULT NULL"
        )
        db.commit()
        print("已添加 user_agents.context_compaction_threshold 列")
    finally:
        db.close()


if __name__ == "__main__":
    main()
