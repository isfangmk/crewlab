"""启动期幂等补列：create_all 只建缺失的表，不会给已有表加新列。

升级既有库（SQLite / PostgreSQL）时，新模型字段需要在这里登记；
每次启动按「表存在且列缺失」补列，重复执行无副作用。
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# (表名, 列名, 列定义)：列定义只用两种方言都支持的语法
_REQUIRED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("user_agents", "sub_agents_json", "TEXT"),
    ("observation_turns", "channel", "VARCHAR(128) NOT NULL DEFAULT ''"),
)


def _add_missing_columns(sync_conn: sa.Connection) -> list[str]:
    """在同步连接上补列，返回实际新增的「表.列」列表。"""

    inspector = sa.inspect(sync_conn)
    tables = set(inspector.get_table_names())
    added: list[str] = []
    for table, column, ddl in _REQUIRED_COLUMNS:
        if table not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column in columns:
            continue
        sync_conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        added.append(f"{table}.{column}")
    return added


async def ensure_schema(engine: AsyncEngine) -> None:
    """补齐 _REQUIRED_COLUMNS 登记的列（必须在 create_all 之后调用）。"""

    async with engine.begin() as conn:
        added = await conn.run_sync(_add_missing_columns)
    if added:
        logger.info("启动期补列完成 columns=%s", added)
