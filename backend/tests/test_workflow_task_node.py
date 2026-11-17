"""画布任务节点：缺省执行器自足跑通（worker 装配缝打桩，编排/落库全真）。

从画布发起的运行没有服务注入的执行上下文，任务节点走缺省执行器——
按节点声明（target/instruction/tools）用默认模型装配只读 worker。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import create_db_engine
from app.models.workflow import Workflow, WorkflowNodeRun, WorkflowRun
from app.schemas.workflow import (
    TaskSpec,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowVariable,
)
from app.services.investigation.worker_runner import WorkerResult
from app.services.workflow import workflow_service


@pytest.mark.asyncio
async def test_canvas_task_node_runs_with_default_runner(monkeypatch) -> None:
    """画布直接运行含任务节点的工作流：缺省执行器装配并跑完，产出进状态。"""

    from app.services.investigation import worker_runner

    async def fake_build_worker_agent(**_kwargs):
        return object()

    async def fake_run_worker(*, worker_uid, name, **_kwargs):
        return WorkerResult(
            worker_uid=worker_uid,
            status="succeeded",
            conclusion=f"{name}的结论",
            findings=[],
            usage={"total_tokens": 10},
            duration_ms=5,
        )

    monkeypatch.setattr(worker_runner, "build_worker_agent", fake_build_worker_agent)
    monkeypatch.setattr(worker_runner, "run_worker", fake_run_worker)

    definition = WorkflowDefinition(
        nodes=[
            WorkflowNode(
                id="start",
                kind="start",
                label="开始",
                x=40,
                y=0,
                variables=[WorkflowVariable(name="question", type="string")],
            ),
            WorkflowNode(
                id="task_1",
                kind="task",
                label="盘点员",
                x=300,
                y=0,
                output_key="task_1_result",
                task=TaskSpec(target="盘点平台能力", instruction="", tools=[]),
            ),
            WorkflowNode(id="end", kind="end", label="结束", x=560, y=0),
        ],
        edges=[
            WorkflowEdge(id="e1", source="start", target="task_1"),
            WorkflowEdge(id="e2", source="task_1", target="end"),
        ],
    )

    engine = create_db_engine(get_settings().database_url)
    workflow_id: int | None = None
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            workflow = Workflow(
                name=f"画布任务测试-{uuid.uuid4().hex[:8]}",
                definition_json=definition.model_dump_json(),
                status="draft",
            )
            db.add(workflow)
            await db.commit()
            await db.refresh(workflow)
            workflow_id = workflow.id

            run = await workflow_service.execute_run(
                db,
                workflow=workflow,
                payload={"question": "盘点"},
                triggered_by="tester",
                source="draft",
            )
            assert run.status == "succeeded"

            node_runs = (
                (
                    await db.scalars(
                        select(WorkflowNodeRun).where(WorkflowNodeRun.run_id == run.id)
                    )
                )
                .all()
            )
            assert {r.node_kind for r in node_runs} == {"start", "task", "end"}
            task_run = next(r for r in node_runs if r.node_kind == "task")
            assert task_run.status == "succeeded"
            # 任务产出写进共享状态：运行输出带上结论
            assert "盘点员的结论" in (run.output_json or "")
    finally:
        await _cleanup(workflow_id)


async def _cleanup(workflow_id: int | None) -> None:
    """插桩数据全清理：节点执行记录、运行记录、工作流行。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if workflow_id is not None:
                runs = (
                    (
                        await db.scalars(
                            select(WorkflowRun).where(
                                WorkflowRun.workflow_id == workflow_id
                            )
                        )
                    )
                    .all()
                )
                if runs:
                    await db.execute(
                        sa_delete(WorkflowNodeRun).where(
                            WorkflowNodeRun.run_id.in_([r.id for r in runs])
                        )
                    )
                    await db.execute(
                        sa_delete(WorkflowRun).where(
                            WorkflowRun.id.in_([r.id for r in runs])
                        )
                    )
                await db.execute(
                    sa_delete(Workflow).where(Workflow.id == workflow_id)
                )
            await db.commit()
    finally:
        await engine.dispose()
