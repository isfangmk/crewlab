"""一次性加列：plan_steps 增加 tool_trace_json（工具调用轨迹持久化）。

代码模型已加字段；SQLite 不会自动 ALTER，历史库需手动补列。
重复执行安全：列已存在则跳过。
用法：.venv/Scripts/python.exe scripts/add_plan_step_tool_trace.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"


def main() -> None:
    db = sqlite3.connect(DB)
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(plan_steps)")}
        if "tool_trace_json" in cols:
            print("tool_trace_json 列已存在，无需处理")
            return
        db.execute(
            "ALTER TABLE plan_steps ADD COLUMN tool_trace_json TEXT NOT NULL DEFAULT '[]'"
        )
        db.commit()
        print("已添加 plan_steps.tool_trace_json 列")
    finally:
        db.close()


if __name__ == "__main__":
    main()
