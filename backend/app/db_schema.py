"""启动时的结构补齐。

Base.metadata.create_all 只会建新表，不会给已存在的表加列。
这里把「新增字段」显式补齐，保证老库升级后能启动。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# (表名, 列名, 列定义)
_REQUIRED_COLUMNS: list[tuple[str, str, str]] = [
    ("llm_providers", "model_prefix", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("llm_providers", "extra_body_json", "TEXT NOT NULL DEFAULT ''"),
    ("chat_sessions", "page_context_json", "TEXT"),
    ("chat_messages", "attachments_json", "TEXT"),
    ("chat_sessions", "pinned", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("chat_sessions", "archived", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("chat_sessions", "owner_user_id", "INTEGER"),
    ("mcp_servers", "approval_policy", "VARCHAR(32) NOT NULL DEFAULT 'always'"),
    ("mcp_servers", "embedding_sync_status", "VARCHAR(32) NOT NULL DEFAULT 'idle'"),
    ("mcp_servers", "embedding_sync_count", "INTEGER NOT NULL DEFAULT 0"),
    ("mcp_servers", "embedding_sync_error", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_servers", "embedding_synced_at", "TIMESTAMPTZ"),
    ("approvals", "decision_reason", "TEXT"),
    ("observation_turns", "tool_call_count", "INTEGER NOT NULL DEFAULT 0"),
    ("observation_turns", "skill_call_count", "INTEGER NOT NULL DEFAULT 0"),
]


async def ensure_columns(conn: AsyncConnection) -> None:
    """补齐新增列；已存在则跳过（幂等）。"""

    for table, column, ddl in _REQUIRED_COLUMNS:
        exists = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        if exists.first() is not None:
            continue
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("已补齐列 table=%s column=%s", table, column)

    # trusted MCP 对齐为 approval_policy=never（仅当列刚可用时校正）
    await conn.execute(
        text(
            "UPDATE mcp_servers SET approval_policy = 'never' "
            "WHERE trusted = TRUE AND approval_policy = 'always'"
        )
    )
