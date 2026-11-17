"""画布任务节点：缺省执行器自足跑通（真依赖，无打桩）。

从画布发起的运行没有服务注入的执行上下文，任务节点走缺省执行器——
按节点声明（target/instruction/tools）用平台默认模型装配只读 agent。
默认 provider 未配置或不可用时 skip（不打桩、不伪造模型输出）。
测试数据在 finally 中全清理，不污染用户数据。
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
from app.services.workflow import workflow_service


@pytest.mark.asyncio
async def test_canvas_task_node_runs_with_default_runner() -> None:
    """画布直接运行含任务节点的工作流：缺省执行器真装配并跑完，产出进状态。"""

    # 真依赖前置：默认 provider 必须配置，否则 skip（禁止打桩冒充）。
    async with async_sessionmaker(
        create_db_engine(get_settings().database_url), expire_on_commit=False
    )() as probe:
        from app.services.platform import llm_config

        try:
            provider_id, model_name = await llm_config.get_default_selection(probe)
            provider = await llm_config.get_provider(probe, provider_id)
        except Exception:  # noqa: BLE001 - 未配默认 provider 时跳过真 LLM 测试
            pytest.skip("未配置可用的默认 provider，跳过画布任务节点真依赖测试")
        if not provider.api_key or not model_name:
            pytest.skip("默认 provider 缺少 api_key/model，跳过")

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
                task=TaskSpec(
                    target="用一句话说明你的角色", instruction="简短作答", tools=["__none__"]
                ),
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
            # 真模型产出写入共享状态：运行输出非空。
            assert (run.output_json or "").strip() not in ("", "{}")
    finally:
        await _cleanup(workflow_id)
        await engine.dispose()


async def _cleanup(workflow_id: int | None) -> None:
    """测试数据全清理：节点执行记录、运行记录、工作流行。"""

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
