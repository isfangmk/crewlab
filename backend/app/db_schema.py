"""启动期幂等补列/删列：create_all 只建缺失的表，不会给已有表加新列。

升级既有库（SQLite / PostgreSQL）时，新模型字段需要在这里登记；
每次启动按「表存在且列缺失」补列、按「表存在且列仍在」删列，重复执行无副作用。
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# (表名, 列名, 列定义)：列定义只用两种方言都支持的语法
_REQUIRED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("user_agents", "display_name", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("user_agents", "sub_agents_json", "TEXT"),
    ("observation_turns", "channel", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("observation_turns", "first_token_ms", "INTEGER"),
    ("artifacts", "message_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("artifacts", "seq", "INTEGER NOT NULL DEFAULT 0"),
    ("knowledge_docs", "external_source_id", "VARCHAR(36)"),
    ("knowledge_docs", "external_key", "VARCHAR(1024)"),
    ("knowledge_docs", "external_hash", "VARCHAR(64)"),
    ("eval_cases", "kind", "VARCHAR(16) NOT NULL DEFAULT 'normal'"),
    ("eval_cases", "attack_type", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("eval_cases", "safety_keywords_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("eval_datasets", "baseline_run_uid", "VARCHAR(32)"),
    ("eval_datasets", "gate_max_regressions", "INTEGER NOT NULL DEFAULT 0"),
    ("eval_datasets", "gate_min_pass_rate", "FLOAT NOT NULL DEFAULT 0.0"),
    ("mcp_servers", "resources_cache_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("mcp_servers", "prompts_cache_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("mcp_servers", "resource_templates_cache_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("mcp_servers", "last_probed_at", "TIMESTAMP"),
)

# (表名, 列名, 删列前的数据修正 SQL)：下线字段先修正存量数据再删列，均幂等
_DEPRECATED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # trusted 与 approval_policy 重复表达免审；砍掉开关后按 trusted 修正存量策略再删列
    ("mcp_servers", "trusted", "UPDATE mcp_servers SET approval_policy = 'never' WHERE trusted"),
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


def _drop_deprecated_columns(sync_conn: sa.Connection) -> list[str]:
    """在同步连接上做数据修正并删列，返回实际删除的「表.列」列表。"""

    inspector = sa.inspect(sync_conn)
    tables = set(inspector.get_table_names())
    dropped: list[str] = []
    for table, column, fix_sql in _DEPRECATED_COLUMNS:
        if table not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in columns:
            continue
        sync_conn.execute(sa.text(fix_sql))
        sync_conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        dropped.append(f"{table}.{column}")
    return dropped


async def ensure_schema(engine: AsyncEngine) -> None:
    """补齐 _REQUIRED_COLUMNS、清理 _DEPRECATED_COLUMNS（必须在 create_all 之后调用）。"""

    async with engine.begin() as conn:
        added = await conn.run_sync(_add_missing_columns)
    if added:
        logger.info("启动期补列完成 columns=%s", added)
    # 删列单独事务：数据修正与删列同事务幂等重跑，互不影响补列
    async with engine.begin() as conn:
        dropped = await conn.run_sync(_drop_deprecated_columns)
    if dropped:
        logger.info("启动期删列完成 columns=%s", dropped)
