"""一次运行的启动与执行分离。

发起运行只做三件事：建记录、登记事件总线、把执行丢到后台。
调用方拿到 run_uid 就能立刻返回或跳转，运行的实际耗时不再挡住响应。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from app.db import SessionLocal
from app.models_orm.workflow import Workflow as WorkflowRow
from app.services import workflow_service
from app.services.workflow_events import workflow_events

logger = logging.getLogger(__name__)

Publish = Callable[[dict[str, Any]], Awaitable[None]]

# 事件循环只弱引用 Task：长运行任务必须自己持有强引用，否则可能被回收
_tasks: set[asyncio.Task[None]] = set()


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
        # 停止标记要在返回 run_uid 之前登记：调用方拿到 uid 就可能立刻点停止
        workflow_service.run_control.begin_run(run_uid)

    publish = workflow_events.open(run_uid)
    _spawn(_run_detached(run_uid, publish))
    return run_uid


def spawn_resume(task: Awaitable[None]) -> None:
    """人工节点续跑也放后台，保证发起方立刻能返回。"""

    _spawn(task)


def _spawn(coro: Awaitable[None]) -> None:
    """登记后台任务，避免被事件循环回收，并在结束时移除引用。"""

    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _run_detached(run_uid: str, publish: Publish) -> None:
    """后台执行：自己开会话，前端断开也不影响落库。"""

    try:
        async with SessionLocal() as db:
            run = await workflow_service.get_run(db, run_uid)
            await workflow_service.execute_existing_run(db, run=run, on_event=publish)
    except Exception as exc:
        logger.exception("后台运行失败 run_uid=%s", run_uid)
        error = f"{type(exc).__name__}: {exc}"
        await _fail_run(run_uid, error)
        await publish(
            {
                "event": "run_finished",
                "run_uid": run_uid,
                "status": workflow_service.RUN_FAILED,
                "error": error,
                "output": "",
                "duration_ms": 0,
                "gate": None,
                "gate_kind": None,
            }
        )


async def _fail_run(run_uid: str, error: str) -> None:
    """执行链路在落库前就断了：把这次运行显式记为失败，事件与库里一致。"""

    async with SessionLocal() as db:
        run = await workflow_service.get_run(db, run_uid)
        run.status = workflow_service.RUN_FAILED
        run.error = error
        run.finished_at = datetime.now(UTC)
        await db.commit()
