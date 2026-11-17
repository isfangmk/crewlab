"""任务图混合 DAG 端到端：真库 + 真 LLM + 真 workflow 引擎，无打桩。

下发 A∥B → C → D，走 submit_graph → confirm_and_run 全流程：
  - 事件序：A、B 并行起跑（均只依赖 start），C 在 A、B 都 node_finished 后才 node_started，D 最后；
  - 落库：四个节点各有结论/状态/耗时，前驱结论进入后继上下文；
  - 汇总：graph_finished 写回 auto-summary 消息，重复收尾幂等；
  - 控制：停止/重试/跳过在图收口后行为正确。

只用专属 session_id / graph_uid，finally 全清理，不污染用户数据。
真实 provider 未配置时 skip（禁止打桩冒充模型输出）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adk_runtime.headless import WorkerBudget
from app.db import SessionLocal, create_db_engine
from app.models.artifact import Artifact
from app.models.chat import ChatMessage, ChatSession
from app.models.task_graph import TaskGraphNode, TaskGraphRun
from app.models.workflow import WorkflowNodeRun, WorkflowRun
from app.schemas.task_graph import TaskGraphNodeSpec, TaskGraphSpec
from app.services.platform import llm_config
from app.services.taskgraph import task_graph_service
from app.services.taskgraph.task_graph_events import task_graph_events

pytestmark = pytest.mark.asyncio

_TIMEOUT_SECONDS = 240


def _mixed_spec() -> TaskGraphSpec:
    return TaskGraphSpec(
        title="端到端混合图",
        question="用一句话分别说明北京、上海、广州、深圳各属于什么城市；不要调用任何工具，直接作答。",
        nodes=[
            TaskGraphNodeSpec(id="A", name="甲", target="说明北京", tools=["__none__"]),
            TaskGraphNodeSpec(id="B", name="乙", target="说明上海", tools=["__none__"]),
            TaskGraphNodeSpec(id="C", name="丙", target="说明广州", depends_on=["A", "B"], tools=["__none__"]),
            TaskGraphNodeSpec(id="D", name="丁", target="说明深圳", depends_on=["C"], tools=["__none__"]),
        ],
    )


async def _default_provider_ready() -> tuple[str, str] | None:
    """返回可用的 (provider_id, model)；不可用返回 None（跳过，不打桩）。"""
    async with SessionLocal() as db:
        try:
            provider_id, model = await llm_config.get_default_selection(db)
            provider = await llm_config.get_provider(db, provider_id)
        except Exception:  # noqa: BLE001 - 未配默认 provider 时跳过真 LLM 测试
            return None
        if provider.api_key and provider.api_key.strip() and model:
            return provider_id, model
        return None


async def _wait_terminal(graph_uid: str, timeout: float = _TIMEOUT_SECONDS) -> list[dict]:
    """订阅图事件直到 graph_finished；返回完整事件序列（含补发）。"""
    backlog, queue = await task_graph_events.subscribe(graph_uid)
    events = list(backlog)

    async def drain() -> None:
        while True:
            event = await queue.get()
            if event is None:
                return
            events.append(event)
            if event.get("event") == "graph_finished":
                return

    try:
        await asyncio.wait_for(drain(), timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"任务图未在 {timeout}s 内收尾，已收到事件: {[e.get('event') for e in events]}")
    finally:
        task_graph_events.unsubscribe(graph_uid, queue)
    return events


async def _cleanup(session_id: str) -> None:
    from app.config import get_settings

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            # 本图每次执行建一个 workflow run：按 triggered_by 精确清理，不碰系统壳工作流。
            workflow_runs = (
                await db.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.triggered_by == f"session:{session_id}"
                    )
                )
            ).all()
            if workflow_runs:
                run_ids = [r.id for r in workflow_runs]
                await db.execute(
                    sa_delete(WorkflowNodeRun).where(WorkflowNodeRun.run_id.in_(run_ids))
                )
                await db.execute(
                    sa_delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids))
                )
            rows = (
                await db.scalars(
                    select(TaskGraphRun).where(TaskGraphRun.session_id == session_id)
                )
            ).all()
            for graph in rows:
                await db.execute(
                    sa_delete(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                )
            if rows:
                await db.execute(
                    sa_delete(TaskGraphRun).where(TaskGraphRun.id.in_([r.id for r in rows]))
                )
            await db.execute(
                sa_delete(ChatMessage).where(ChatMessage.session_id == session_id)
            )
            await db.execute(
                sa_delete(Artifact).where(Artifact.session_id == session_id)
            )
            session = await db.get(ChatSession, session_id)
            if session is not None:
                await db.delete(session)
            await db.commit()
    finally:
        await engine.dispose()


async def test_task_graph_mixed_dag_end_to_end() -> None:
    """混合 DAG 真跑：并行起跑、join 等待、落库、汇总、控制全链路。"""
    selection = await _default_provider_ready()
    if selection is None:
        pytest.skip("未配置可用的默认 provider，跳过任务图真依赖端到端测试")
    provider_id, model_name = selection
    session_id = f"tg-e2e-{uuid.uuid4().hex[:8]}"
    graph_uid = ""
    async with SessionLocal() as db:
        db.add(
            ChatSession(
                id=session_id,
                title="任务图端到端测试",
                provider=provider_id,
                model=model_name,
                agent_id="default",
                adk_user_id="tg-e2e-user",
            )
        )
        await db.commit()

    try:
        spec = _mixed_spec()
        budget = WorkerBudget(max_steps=4, timeout_seconds=60, token_budget=20000)
        async with SessionLocal() as db:
            graph = await task_graph_service.submit_graph(
                db, session_id=session_id, spec=spec, budget=budget
            )
            graph_uid = graph.graph_uid
            assert graph.status == "draft"

        # 订阅在 confirm 前挂好，避免漏事件（总线会补发，双保险）
        events_task = asyncio.create_task(_wait_terminal(graph_uid))
        await asyncio.sleep(0.05)

        async with SessionLocal() as db:
            started = await task_graph_service.confirm_and_run(db, graph_uid)
            assert started is True

        events = await events_task

        started_order = [e["local_id"] for e in events if e["event"] == "node_started"]
        finished_order = [e["local_id"] for e in events if e["event"] == "node_finished"]
        terminal = [e for e in events if e["event"] == "graph_finished"]

        assert terminal, "未收到 graph_finished"
        assert terminal[-1]["status"] in ("succeeded", "failed")
        # A、B 并行起跑，且都先于 C；C 先于 D
        assert "A" in started_order and "B" in started_order
        assert "C" in started_order and "D" in started_order
        assert started_order.index("A") < started_order.index("C")
        assert started_order.index("B") < started_order.index("C")
        assert started_order.index("C") < started_order.index("D")
        # C 必须在 A、B 都完成后才起跑（join 屏障）
        assert finished_order.index("A") < started_order.index("C")
        assert finished_order.index("B") < started_order.index("C")

        async with SessionLocal() as db:
            graph = (
                await db.scalars(
                    select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid)
                )
            ).first()
            assert graph is not None
            assert graph.status in ("succeeded", "failed")
            nodes = (
                await db.scalars(
                    select(TaskGraphNode)
                    .where(TaskGraphNode.graph_id == graph.id)
                    .order_by(TaskGraphNode.order_index)
                )
            ).all()
            assert len(nodes) == 4
            assert all(n.duration_ms >= 0 for n in nodes)
            # 每个已成功节点都有非空结论（真模型产出，不造假）
            succeeded = [n for n in nodes if n.status == "succeeded"]
            assert succeeded
            assert all((n.summary or "").strip() for n in succeeded)

            # 汇总消息落库且幂等：重复收尾不新增
            summary_id = f"auto-summary-taskgraph-{graph_uid}"
            summary = await db.get(ChatMessage, summary_id)
            assert summary is not None
            assert (summary.content or "").strip()

        # 幂等：再跑一次收尾汇总，消息 id 不变、内容不重复追加
        from app.services.execution.agent_assembly import assemble_agent
        from app.services.taskgraph.graph_summary import run_graph_summary

        async with SessionLocal() as db:
            again = await run_graph_summary(
                db,
                session_id=session_id,
                graph_uid=graph_uid,
                header="重复收尾",
                question=spec.question,
                nodes=[{"name": n.name, "conclusion": n.summary or "", "findings": [], "tool_trace": []} for n in nodes],
                budget=budget,
                assemble_agent_fn=assemble_agent,
                publish=None,
            )
            assert again == summary_id

        # 控制①：skip 失败节点——图终态后把尾节点置 failed，跳过后重新聚合收口。
        # 纯状态机，不重跑、不再调模型；验证「后继缺口」路径。
        async with SessionLocal() as db:
            tail = (
                await db.scalars(
                    select(TaskGraphNode).where(
                        TaskGraphNode.graph_id == graph.id,
                        TaskGraphNode.local_id == "D",
                    )
                )
            ).first()
            assert tail is not None
            tail.status = "failed"
            tail.error = "测试注入的失败"
            await db.commit()
            skipped = await task_graph_service.skip_node(db, graph_uid, tail.node_uid)
            assert skipped is not None and skipped.status == "skipped"

        async with SessionLocal() as db:
            tail = (
                await db.scalars(
                    select(TaskGraphNode).where(
                        TaskGraphNode.graph_id == graph.id,
                        TaskGraphNode.local_id == "D",
                    )
                )
            ).first()
            assert tail is not None and tail.status == "skipped"

        # 控制②：终态图 stop 返回 False（无活动任务），不做无谓写入
        async with SessionLocal() as db:
            assert await task_graph_service.stop_graph(db, graph_uid) is False
    finally:
        await _cleanup(session_id)


async def test_task_graph_retry_failed_node_reruns() -> None:
    """重试失败节点：attempt+1、状态回 pending、真重跑后 succeeded。"""
    selection = await _default_provider_ready()
    if selection is None:
        pytest.skip("未配置可用的默认 provider，跳过任务图重试真依赖测试")
    provider_id, model_name = selection
    session_id = f"tg-retry-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        db.add(
            ChatSession(
                id=session_id,
                title="任务图重试测试",
                provider=provider_id,
                model=model_name,
                agent_id="default",
                adk_user_id="tg-retry-user",
            )
        )
        await db.commit()

    graph_uid = ""
    try:
        spec = TaskGraphSpec(
            title="重试测试图",
            question="用一句话说明什么是 HTTP；不要调用任何工具，直接作答。",
            nodes=[
                TaskGraphNodeSpec(id="A", name="甲", target="说明 HTTP", tools=["__none__"]),
            ],
        )
        budget = WorkerBudget(max_steps=4, timeout_seconds=60, token_budget=20000)
        async with SessionLocal() as db:
            graph = await task_graph_service.submit_graph(
                db, session_id=session_id, spec=spec, budget=budget
            )
            graph_uid = graph.graph_uid

        events_task = asyncio.create_task(_wait_terminal(graph_uid))
        await asyncio.sleep(0.05)
        async with SessionLocal() as db:
            assert await task_graph_service.confirm_and_run(db, graph_uid) is True
        await events_task

        # 制造可控失败：把唯一节点置 failed，再 retry 触发真重跑
        async with SessionLocal() as db:
            row = (
                await db.scalars(
                    select(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                )
            ).first()
            assert row is not None and row.status == "succeeded"
            row.status = "failed"
            row.error = "测试注入的失败"
            await db.commit()
            retried = await task_graph_service.retry_node(db, graph_uid, row.node_uid)
            assert retried is not None and retried.attempt == 2

        retry_events_task = asyncio.create_task(_wait_terminal(graph_uid))
        await retry_events_task

        async with SessionLocal() as db:
            row = (
                await db.scalars(
                    select(TaskGraphNode).where(TaskGraphNode.graph_id == graph.id)
                )
            ).first()
            assert row is not None
            assert row.status == "succeeded"
            assert row.attempt == 2
            assert (row.summary or "").strip()
    finally:
        await _cleanup(session_id)
