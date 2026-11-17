"""一次性加列：chat_messages 增加 interrupted（标记插话打断写入的消息）。

代码模型已加字段；SQLite 不会自动 ALTER，历史库需手动补列。
重复执行安全：列已存在则跳过。
用法：.venv/Scripts/python.exe scripts/add_chat_message_interrupted.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"


def main() -> None:
    db = sqlite3.connect(DB)
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(chat_messages)")}
        if "interrupted" in cols:
            print("interrupted 列已存在，无需处理")
            return
        db.execute(
            "ALTER TABLE chat_messages ADD COLUMN interrupted BOOLEAN NOT NULL DEFAULT 0"
        )
        db.commit()
        print("已添加 chat_messages.interrupted 列")
    finally:
        db.close()


if __name__ == "__main__":
    main()
