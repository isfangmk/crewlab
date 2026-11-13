"""沙盒执行记录的查询：列表、详情、汇总。

列表页要能按来源、状态、时间、关键字筛选，并做服务端分页：
执行记录涨得快，页面自己过滤会把整个表拉出来。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.sandbox import SandboxExecution

logger = logging.getLogger(__name__)

# 允许筛选的来源与状态；传别的值直接报错，不静默返回空列表
SOURCES = ("skill", "workflow", "manual")
STATUSES = ("succeeded", "failed", "timeout")

MAX_LIMIT = 200


def _filters(
    *,
    q: str | None,
    source: str | None,
    status: str | None,
    session_id: str | None,
    workflow_run_id: str | None,
    started_from: datetime | None,
    started_to: datetime | None,
) -> list[Any]:
    conditions: list[Any] = []
    if source is not None:
        if source not in SOURCES:
            raise ValueError(f"不支持的 source: {source}（可选 {list(SOURCES)}）")
        conditions.append(SandboxExecution.source == source)
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"不支持的 status: {status}（可选 {list(STATUSES)}）")
        conditions.append(SandboxExecution.status == status)
    if session_id:
        conditions.append(SandboxExecution.session_id == session_id)
    if workflow_run_id:
        conditions.append(SandboxExecution.workflow_run_id == workflow_run_id)
    if started_from is not None:
        conditions.append(SandboxExecution.started_at >= started_from)
    if started_to is not None:
        conditions.append(SandboxExecution.started_at <= started_to)
    if q:
        # 关键字同时匹配来源标识与输出正文：
        # 用户往往是记得「上次那段代码报了个什么错」才来查的。
        needle = f"%{q}%"
        conditions.append(
            or_(
                SandboxExecution.source_ref.ilike(needle),
                SandboxExecution.code.ilike(needle),
                SandboxExecution.stdout.ilike(needle),
                SandboxExecution.stderr.ilike(needle),
                SandboxExecution.error.ilike(needle),
                SandboxExecution.session_id.ilike(needle),
                SandboxExecution.workflow_run_id.ilike(needle),
            )
        )
    return conditions


async def list_executions(
    db: AsyncSession,
    *,
    q: str | None = None,
    source: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    workflow_run_id: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SandboxExecution], int]:
    """执行记录列表 + 筛选后的总数。"""

    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit 必须在 1..{MAX_LIMIT} 之间")
    if offset < 0:
        raise ValueError("offset 不能为负")

    conditions = _filters(
        q=q,
        source=source,
        status=status,
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        started_from=started_from,
        started_to=started_to,
    )
    stmt = select(SandboxExecution)
    count_stmt = select(func.count()).select_from(SandboxExecution)
    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)
    stmt = stmt.order_by(SandboxExecution.started_at.desc(), SandboxExecution.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    rows = list((await db.scalars(stmt)).all())
    total = int(await db.scalar(count_stmt) or 0)
    return rows, total


async def get_execution(db: AsyncSession, execution_id: str) -> SandboxExecution:
    row = await db.get(SandboxExecution, execution_id)
    if row is None:
        raise LookupError(f"沙盒执行记录不存在: {execution_id}")
    return row


async def summarize(db: AsyncSession) -> dict[str, Any]:
    """概览：各来源与各状态的数量、平均耗时。"""

    by_source_rows = await db.execute(
        select(SandboxExecution.source, func.count())
        .group_by(SandboxExecution.source)
    )
    by_status_rows = await db.execute(
        select(SandboxExecution.status, func.count())
        .group_by(SandboxExecution.status)
    )
    avg_row = await db.execute(
        select(func.avg(SandboxExecution.duration_ms)).select_from(SandboxExecution)
    )
    total_row = await db.execute(
        select(func.count()).select_from(SandboxExecution)
    )
    return {
        "total": int(total_row.scalar() or 0),
        "by_source": {str(k): int(v) for k, v in by_source_rows.all()},
        "by_status": {str(k): int(v) for k, v in by_status_rows.all()},
        "avg_duration_ms": int(avg_row.scalar() or 0),
    }
