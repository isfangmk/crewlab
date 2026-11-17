"""表结构变更：建统一任务图两张新表，删除 plan/investigation 四张旧表。

这是表结构变更（加表 + 删表），不是业务数据修复，符合"仅在表结构变更时写脚本"的规则。
历史数据不迁移——新表从空开始，旧表连同历史一起删除。

应用启动时 Base.metadata.create_all 也会补建新表；脚本额外负责删旧表，
并保证在重启前新表已就绪。重复执行安全。

用法：.venv/Scripts/python.exe scripts/db/20261117_task_graph_create.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "ai_workbench.db"

CREATE_RUN = """
CREATE TABLE IF NOT EXISTS task_graph_runs (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    graph_uid VARCHAR(64) NOT NULL UNIQUE,
    session_id VARCHAR(36) NOT NULL,
    card_message_id VARCHAR(128) NOT NULL DEFAULT '',
    title VARCHAR(256) NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    spec_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL DEFAULT '{}',
    findings_json TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    started_at DATETIME,
    finished_at DATETIME,
    FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
)
"""
CREATE_RUN_INDEX_UID = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_task_graph_runs_graph_uid "
    "ON task_graph_runs (graph_uid)"
)
CREATE_RUN_INDEX_SESSION = (
    "CREATE INDEX IF NOT EXISTS ix_task_graph_runs_session_id "
    "ON task_graph_runs (session_id)"
)

CREATE_NODE = """
CREATE TABLE IF NOT EXISTS task_graph_nodes (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    graph_id INTEGER NOT NULL,
    node_uid VARCHAR(64) NOT NULL UNIQUE,
    local_id VARCHAR(64) NOT NULL DEFAULT '',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    order_index INTEGER NOT NULL DEFAULT 0,
    name VARCHAR(128) NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    tools_json TEXT NOT NULL DEFAULT '[]',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 1,
    summary TEXT NOT NULL DEFAULT '',
    findings_json TEXT NOT NULL DEFAULT '[]',
    tool_trace_json TEXT NOT NULL DEFAULT '[]',
    log_text TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    started_at DATETIME,
    finished_at DATETIME,
    FOREIGN KEY(graph_id) REFERENCES task_graph_runs (id) ON DELETE CASCADE
)
"""
CREATE_NODE_INDEX_GRAPH = (
    "CREATE INDEX IF NOT EXISTS ix_task_graph_nodes_graph_id "
    "ON task_graph_nodes (graph_id)"
)
CREATE_NODE_INDEX_UID = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_task_graph_nodes_node_uid "
    "ON task_graph_nodes (node_uid)"
)

OLD_TABLES = (
    "investigation_worker_runs",
    "investigation_dispatches",
    "plan_steps",
    "plan_executions",
)


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"数据库不存在: {DB}")
    db = sqlite3.connect(DB)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        for ddl in (
            CREATE_RUN,
            CREATE_RUN_INDEX_UID,
            CREATE_RUN_INDEX_SESSION,
            CREATE_NODE,
            CREATE_NODE_INDEX_GRAPH,
            CREATE_NODE_INDEX_UID,
        ):
            db.execute(ddl)
        print("新表就绪: task_graph_runs / task_graph_nodes")

        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in OLD_TABLES:
            if table in existing:
                db.execute(f"DROP TABLE IF EXISTS {table}")
                print(f"已删除旧表: {table}")
        db.commit()
        print("完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()
