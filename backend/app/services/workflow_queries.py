"""工作流列表与运行记录的批量查询。

API 路由和内置工具都要按工作流 ID 批量取名字、取最近一次运行，
放在同一处避免两处各写一份等价 SQL。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.workflow import Workflow as WorkflowRow
from app.models_orm.workflow import WorkflowRun as WorkflowRunRow


async def workflow_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    """批量取工作流名称；运行记录里只存了 id，展示需要名字。"""

    if len(ids) == 0:
        return {}
    result = await db.execute(
        select(WorkflowRow.id, WorkflowRow.name).where(WorkflowRow.id.in_(ids))
    )
    return {row_id: name for row_id, name in result.all()}


async def latest_runs(db: AsyncSession, ids: set[int]) -> dict[int, Any]:
    """每个工作流最近一次运行：一次查完避免逐行查询。"""

    if len(ids) == 0:
        return {}
    latest_ids = (
        select(func.max(WorkflowRunRow.id))
        .where(WorkflowRunRow.workflow_id.in_(ids))
        .group_by(WorkflowRunRow.workflow_id)
    )
    rows = (
        await db.scalars(select(WorkflowRunRow).where(WorkflowRunRow.id.in_(latest_ids)))
    ).all()
    return {run.workflow_id: run for run in rows}
