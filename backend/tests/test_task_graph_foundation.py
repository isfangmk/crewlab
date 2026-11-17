"""任务图地基与编排：真库验证图定义编译、草稿 CRUD、事件补发。

不 mock DB、不 mock LLM——本模块只验证不依赖模型的图结构与状态机部分。
含真实 LLM 的端到端调度验证在 test_task_graph_e2e.py（单独、真依赖）。
测试用专属 session_id，结束后清理，不污染用户数据。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.adk_runtime.headless import WorkerBudget
from app.db import SessionLocal
from app.models.chat import ChatSession
from app.models.task_graph import TaskGraphNode, TaskGraphRun
from app.schemas.task_graph import TaskGraphEdgeSpec, TaskGraphNodeSpec, TaskGraphSpec
from app.services.taskgraph import graph_builder, task_graph_service
from app.services.taskgraph.task_graph_events import TaskGraphEventBus


def _mixed_spec() -> TaskGraphSpec:
    """A∥B → C → D 的混合图。"""

    return TaskGraphSpec(
        title="混合图",
        question="验证并行与汇合",
        nodes=[
            TaskGraphNodeSpec(id="A", name="甲", target="查 A"),
            TaskGraphNodeSpec(id="B", name="乙", target="查 B"),
            TaskGraphNodeSpec(id="C", name="丙", target="汇总", depends_on=["A", "B"]),
            TaskGraphNodeSpec(id="D", name="丁", target="收口", depends_on=["C"]),
        ],
    )


def test_graph_builder_mixed_dag_shape() -> None:
    spec = _mixed_spec()
    definition = graph_builder.build_definition(spec)
    ids = {n.id for n in definition.nodes}
    assert {"start", "A", "B", "C", "D", "join_C", "end"} <= ids
    edge_pairs = {(e.source, e.target) for e in definition.edges}
    # A、B 并行从 start 扇出
    assert ("start", "A") in edge_pairs
    assert ("start", "B") in edge_pairs
    # C 经 join 等 A、B 全部完成
    assert ("A", "join_C") in edge_pairs
    assert ("B", "join_C") in edge_pairs
    assert ("join_C", "C") in edge_pairs
    # D 串行依赖 C
    assert ("C", "D") in edge_pairs


def test_graph_spec_validation() -> None:
    from pydantic import ValidationError

    # 环被拒
    with pytest.raises(ValidationError):
        TaskGraphSpec(
            nodes=[
                TaskGraphNodeSpec(id="A", target="x", depends_on=["B"]),
                TaskGraphNodeSpec(id="B", target="y", depends_on=["A"]),
            ]
        )
    # 保留字被拒
    with pytest.raises(ValidationError):
        TaskGraphNodeSpec(id="start", target="x")
    # 显式边合并进 depends_on
    spec = TaskGraphSpec(
        nodes=[
            TaskGraphNodeSpec(id="A", target="x"),
            TaskGraphNodeSpec(id="B", target="y"),
        ],
        edges=[TaskGraphEdgeSpec(source="A", target="B")],
    )
    assert spec.dependencies_of("B") == ("A",)


async def test_event_bus_backfill_and_terminal() -> None:
    bus = TaskGraphEventBus()
    uid = str(uuid.uuid4())
    publish = bus.open(uid)
    await publish({"event": "graph_started", "graph_uid": uid})
    backlog, queue = await bus.subscribe(uid)
    assert any(e["event"] == "graph_started" for e in backlog)
    await publish({"event": "node_finished", "graph_uid": uid, "status": "succeeded"})
    item = await queue.get()
    assert item["event"] == "node_finished"
    await publish({"event": "graph_finished", "graph_uid": uid})
    terminal = await queue.get()
    assert terminal["event"] == "graph_finished"
    assert await queue.get() is None  # 终态后断开哨兵
    assert bus.is_finished(uid)


async def test_submit_and_update_graph_draft() -> None:
    session_id = f"tg-test-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        # 真库需要有会话行满足外键；测试结束清理
        db.add(
            ChatSession(
                id=session_id,
                title="任务图测试",
                provider="test",
                model="test",
                adk_user_id="tg-test-user",
            )
        )
        await db.commit()
    try:
        spec = _mixed_spec()
        budget = WorkerBudget(max_steps=3, timeout_seconds=30, token_budget=1000)
        async with SessionLocal() as db:
            graph = await task_graph_service.submit_graph(
                db, session_id=session_id, spec=spec, budget=budget
            )
            graph_uid = graph.graph_uid
            assert graph.status == "draft"

        async with SessionLocal() as db:
            nodes = (
                await db.scalars(
                    select(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                )
            ).all()
            assert len(nodes) == 4
            by_local = {n.local_id: n for n in nodes}
            assert by_local["C"].local_id == "C"

        # 整图替换：A → B（2 节点）
        new_spec = TaskGraphSpec(
            title="新图",
            question="改图",
            nodes=[
                TaskGraphNodeSpec(id="A", target="x"),
                TaskGraphNodeSpec(id="B", target="y", depends_on=["A"]),
            ],
        )
        async with SessionLocal() as db:
            ok = await task_graph_service.update_spec(db, graph_uid, new_spec)
            assert ok is True
            nodes = (
                await db.scalars(
                    select(TaskGraphNode).where(
                        TaskGraphNode.graph_id == (
                            await db.scalars(
                                select(TaskGraphRun.id).where(
                                    TaskGraphRun.graph_uid == graph_uid
                                )
                            )
                        ).first()
                    )
                )
            ).all()
            assert len(nodes) == 2
    finally:
        async with SessionLocal() as db:
            graph = (
                await db.scalars(
                    select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid)
                )
            ).first()
            if graph is not None:
                await db.delete(graph)  # 级联删节点
            session = await db.get(ChatSession, session_id)
            if session is not None:
                await db.delete(session)
            await db.commit()
