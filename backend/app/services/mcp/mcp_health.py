"""MCP Server 后台探活：周期性轻量健康检查，实时维护状态三字段。

与手动「测试」分工：探活只管 status / last_error / last_probed_at，
工具缓存、附加能力与嵌入同步留给手动全量刷新。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import McpServer
from app.services.mcp.mcp_service import (
    clear_unhealthy_warn,
    probe_server_health,
    warn_unhealthy_server,
)

logger = logging.getLogger(__name__)

# 探活间隔：状态新鲜度与外部服务压力的平衡
MCP_HEALTH_INTERVAL_SECONDS = 30

_task: asyncio.Task | None = None


async def probe_enabled_servers_once(db: AsyncSession) -> int:
    """探活一轮所有启用的 Server，返回存活台数；单台失败不影响其他台。"""

    rows = (await db.scalars(select(McpServer).where(McpServer.enabled.is_(True)))).all()
    alive = 0
    for row in rows:
        try:
            if await probe_server_health(db, row):
                alive += 1
                # 恢复后清掉告警记忆，便于下次故障重新 WARNING
                clear_unhealthy_warn(row.id)
            else:
                warn_unhealthy_server(row, site="health_loop")
        except Exception:  # noqa: BLE001 — 单台异常不断整轮
            logger.exception("MCP 探活异常 id=%s name=%s", row.id, row.name)
    return alive


async def _loop() -> None:
    while True:
        try:
            from app.db import SessionLocal

            async with SessionLocal() as db:
                await probe_enabled_servers_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 探活失败不拖垮进程，下一轮重试
            logger.exception("MCP 探活循环异常，下一轮重试")
        await asyncio.sleep(MCP_HEALTH_INTERVAL_SECONDS)


async def start_mcp_health() -> None:
    """起后台探活循环（首轮即跑）。"""

    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("MCP 探活循环已启动 interval=%ss", MCP_HEALTH_INTERVAL_SECONDS)


async def stop_mcp_health() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    logger.info("MCP 探活循环已停止")
