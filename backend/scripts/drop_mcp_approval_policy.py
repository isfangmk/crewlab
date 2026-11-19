"""一次性删列：mcp_servers.approval_policy。

审批策略已删除，MCP 工具改为「审批规则驱动，无规则免审」。代码模型已移除该列；
SQLite 的 create_all 不会删列，PostgreSQL 已有库需手动删列，否则 NOT NULL 无默认值
会让新增 MCP Server 的 INSERT 失败。

重复执行安全：列不存在则跳过。
用法：.venv/Scripts/python.exe scripts/drop_mcp_approval_policy.py
"""

from __future__ import annotations

import asyncio

import asyncpg

from app.config import get_settings


async def main() -> None:
    url = get_settings().database_url
    if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
        print(f"非 PostgreSQL 数据库（{url.split('://')[0]}），无需删列")
        return

    # asyncpg 直接吃 postgresql://（去掉 SQLAlchemy 的 +asyncpg 方言标记）
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'mcp_servers' AND column_name = 'approval_policy'
            )
            """
        )
        if not exists:
            print("mcp_servers.approval_policy 列不存在，无需处理")
            return
        await conn.execute("ALTER TABLE mcp_servers DROP COLUMN approval_policy")
        print("已删除 mcp_servers.approval_policy 列")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
