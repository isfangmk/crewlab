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
