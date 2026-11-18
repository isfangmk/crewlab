"""一次性加性能索引（表结构变更）：chat_messages 的会话过滤/排序列。

根因：会话列表默认排序用相关子查询 max(chat_messages.created_at) WHERE session_id=?，
消息列表按 session_id 过滤再 ORDER BY seq；两列原本都无索引，每次全表扫描 chat_messages。
压测证实：会话列表 980ms→0.4ms（覆盖索引），消息列表 4.4ms→0.3ms。

SQLite 不会从模型自动建库外索引，历史库需手动补。重复执行安全（IF NOT EXISTS）。
用法：.venv/Scripts/python.exe scripts/add_perf_indexes.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"

# (索引名, SQL)；只加压测证实有收益的，不盲目全加
INDEXES = [
    (
        "ix_chat_messages_session_created",
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created "
        "ON chat_messages (session_id, created_at)",
    ),
    (
        "ix_chat_messages_session_seq",
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_session_seq "
        "ON chat_messages (session_id, seq)",
    ),
]


def main() -> None:
    db = sqlite3.connect(DB)
    try:
        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        for name, sql in INDEXES:
            if name in existing:
                print(f"{name} 已存在，跳过")
                continue
            db.execute(sql)
            print(f"已创建 {name}")
        db.commit()

        # EXPLAIN 验证：相关子查询应显示 USING COVERING INDEX
        plan = list(
            db.execute(
                "EXPLAIN QUERY PLAN SELECT s.id FROM chat_sessions s "
                "WHERE s.archived=0 ORDER BY coalesce("
                "(SELECT max(created_at) FROM chat_messages m WHERE m.session_id=s.id),"
                "s.created_at) DESC LIMIT 20"
            )
        )
        print("EXPLAIN 验证：")
        for row in plan:
            print("  ", row[3])
    finally:
        db.close()


if __name__ == "__main__":
    main()
