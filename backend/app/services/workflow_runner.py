"""一次运行的启动与执行分离。

发起运行只做三件事：建记录、登记事件总线、把执行丢到后台。
调用方拿到 run_uid 就能立刻返回或跳转，运行的实际耗时不再挡住响应。
"""

from __future__ import annotations

import asyncio
import logging

from app.db import SessionLocal
from app.models_orm.workflow import Workflow as WorkflowRow
from app.services import workflow_service
from app.services.workflow_events import workflow_events

logger = logging.getLogger(__name__)


async def start_run(
    *,
    workflow_id: int,
    payload: dict[str, object],
    triggered_by: str,
    source: str,
) -> str:
    """建立运行记录并在后台开跑，立刻返回 run_uid。"""

    async with SessionLocal() as db:
        workflow = await db.get(WorkflowRow, workflow_id)
        if workflow is None:
            raise LookupError(f"工作流不存在: {workflow_id}")
        run, _definition = await workflow_service.create_run(
            db,
            workflow=workflow,
            payload=payload,
            triggered_by=triggered_by,
            source=source,
        )
        run_uid = run.run_uid

    # 先登记总线再起任务：订阅方可能比执行先到，缓冲为空也不能报错
    publish = workflow_events.open(run_uid)
    asyncio.create_task(_run_detached(run_uid, publish))
    return run_uid


async def _run_detached(run_uid: str, publish: object) -> None:
    """后台执行：自己开会话，前端断开也不影响落库。"""

    try:
        async with SessionLocal() as db:
            run = await workflow_service.get_run(db, run_uid)
            await workflow_service.execute_existing_run(db, run=run, on_event=publish)  # type: ignore[arg-type]
    except Exception as exc:
        logger.exception("后台运行失败 run_uid=%s", run_uid)
        # _drive 内部已把失败落库并推过终态；这里只兜住「连终态都没推出去」的情况，
        # 否则订阅方会一直等下去。
        channel = workflow_events.channel(run_uid)
        if not channel.finished:
            channel.publish({"event": "run_failed", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        workflow_events.channel(run_uid).close()
