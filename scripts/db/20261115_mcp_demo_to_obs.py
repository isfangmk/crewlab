"""数据库变更脚本：demo-tools MCP 迁移为 obs-tools 可观测平台（2026-11-15）。

demo-tools（add/echo/current_time 演示工具）改造为可观测 MCP（指标/链路/日志/
CMDB/告警/事件 25 个工具），注册行改名 obs-tools、审批策略改 non_readonly
（查询工具免审、写工具走审批），清掉旧工具缓存与向量（下次「测试连接」重灌）。
workflows.definition_json 里的 mcp_server 引用一次改齐。幂等：改过即跳过。

运行（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/db/20261115_mcp_demo_to_obs.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# DATABASE_URL 是相对路径（./data/...），必须以 backend/ 为工作目录，和后端进程一致
from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / "backend" / ".env")
os.chdir(_REPO_ROOT / "backend")
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import sqlalchemy as sa


async def main() -> None:
    from app.db import engine, SessionLocal
    from app.services.mcp.tool_search_index import forget_server_vectors

    async with engine.begin() as conn:

        def _migrate(sync_conn: sa.Connection) -> int | None:
            inspector = sa.inspect(sync_conn)
            tables = inspector.get_table_names()
            if "mcp_servers" not in tables:
                print("mcp_servers 表不存在，跳过")
                return None
            row = sync_conn.execute(
                sa.text("SELECT id, name FROM mcp_servers WHERE name IN ('demo-tools', 'obs-tools')")
            ).fetchone()
            if row is None:
                print("demo-tools / obs-tools 注册行不存在，跳过")
                return None
            if row.name == "obs-tools":
                print("已是 obs-tools，跳过")
                return row.id
            sync_conn.execute(
                sa.text(
                    "UPDATE mcp_servers SET name='obs-tools', "
                    "approval_policy='non_readonly', tools_cache_json='[]', "
                    "status='pending', last_error='', "
                    "resources_cache_json='[]', prompts_cache_json='[]', "
                    "resource_templates_cache_json='[]', "
                    "embedding_sync_status='idle', embedding_sync_count=0, "
                    "embedding_sync_error='' WHERE id=:id"
                ),
                {"id": row.id},
            )
            if "workflows" in tables:
                sync_conn.execute(
                    sa.text(
                        "UPDATE workflows SET definition_json = "
                        "REPLACE(definition_json, 'demo-tools', 'obs-tools') "
                        "WHERE definition_json LIKE '%demo-tools%'"
                    )
                )
            print(f"mcp_servers #{row.id} demo-tools → obs-tools（non_readonly）")
            return row.id

        server_id = await conn.run_sync(_migrate)

    if server_id is not None:
        # 旧工具（add/echo/current_time）向量清掉，防止残留参与检索
        async with SessionLocal() as session:
            n = await forget_server_vectors(session, server_id)
            await session.execute(
                sa.text("DELETE FROM mcp_tool_embeddings WHERE server_id=:id"),
                {"id": server_id},
            )
            await session.commit()
            print(f"已清旧工具向量 {n} 条（下次「测试连接」重灌新工具向量）")


if __name__ == "__main__":
    asyncio.run(main())
