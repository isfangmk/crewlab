"""任务图编排服务：草稿确认 → 建 run → DAG 调度 → 状态聚合 → 收尾汇总。

生命周期与旧 plan/investigation 同口径，但一套服务覆盖并行/串行/混合全部拓扑：
  - submit_graph 建草稿（不执行），update_spec 仅草稿可改，confirm_and_run 确认执行；
  - 展开 WorkflowDefinition 后经 workflow_service 建运行、注入 task_runner 调度；
  - 节点是 DAG 上的任务，入度 0 并行起跑，多前驱经 join 屏障等全部完成；
  - 终态统一走 graph_summary（带工具/出图/流式/stats），写回对话流；
  - 停止/重试/跳过按图口径收口，状态写库与事件发布分离、锁内聚合防丢失更新。

关键业务路径打日志：提交/确认/收尾/停止/重试/跳过都记录 graph_uid 与结果。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.headless import WorkerBudget
from app.db import SessionLocal
from app.models.task_graph import TaskGraphNode, TaskGraphRun
from app.schemas.task_graph import TaskGraphSpec
from app.services.platform.task_control import StopSignal
from app.services.taskgraph import graph_builder
from app.services.taskgraph.node_executor import execute_node
from app.services.taskgraph.task_graph_events import task_graph_events

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()

# 系统编排标识（新架构独立一套，不复用旧 plan/investigation 常量）
SYSTEM_WORKFLOW_TASK_GRAPH = "系统 · 任务图执行"
RUN_TRIGGER_TASK_GRAPH = "task_graph"

_TERMINAL_NODE = ("succeeded", "failed", "skipped", "stopped")
_TERMINAL_GRAPH = ("succeeded", "failed", "stopped")


@dataclass
class _GraphRuntime:
    graph_uid: str
    stop: StopSignal
    stop_requested: bool = False
    running: bool = False
    node_uids: list[str] = field(default_factory=list)


_runtimes: dict[str, _GraphRuntime] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}


def _refresh_lock(graph_uid: str) -> asyncio.Lock:
    return _refresh_locks.setdefault(graph_uid, asyncio.Lock())


def _ensure_runtime(graph_uid: str) -> _GraphRuntime:
    runtime = _runtimes.get(graph_uid)
    if runtime is None:
        runtime = _GraphRuntime(
            graph_uid=graph_uid, stop=StopSignal(f"task_graph:{graph_uid}")
        )
        _runtimes[graph_uid] = runtime
    return runtime


def _budget_of(graph: TaskGraphRun) -> WorkerBudget:
    config = json.loads(graph.config_json or "{}")
    defaults = WorkerBudget()
    return WorkerBudget(
        max_steps=int(config.get("max_steps_per_node") or defaults.max_steps),
        timeout_seconds=int(config.get("timeout_seconds_per_node") or defaults.timeout_seconds),
        token_budget=int(config.get("token_budget_per_node") or defaults.token_budget),
    )


# ---------------------------------------------------------------------------
# 草稿与提交
# ---------------------------------------------------------------------------


async def submit_graph(
    db: AsyncSession,
    *,
    session_id: str,
    spec: TaskGraphSpec,
    budget: WorkerBudget,
    card_message_id: str = "",
    created_by: int | None = None,
) -> TaskGraphRun:
    """建草稿图与节点行，不执行。节点 local_id/depends_on 落库供渲染与调度。"""

    import uuid

    graph_uid = str(uuid.uuid4())
    graph = TaskGraphRun(
        graph_uid=graph_uid,
        session_id=session_id,
        card_message_id=card_message_id,
        title=spec.title[:256],
        question=spec.question,
        status="draft",
        spec_json=spec.model_dump_json(),
        config_json=json.dumps(
            {
                "max_steps_per_node": budget.max_steps,
                "timeout_seconds_per_node": budget.timeout_seconds,
                "token_budget_per_node": budget.token_budget,
            },
            ensure_ascii=False,
        ),
        created_by=created_by,
    )
    db.add(graph)
    await db.flush()

    for index, node_spec in enumerate(spec.nodes):
        db.add(
            TaskGraphNode(
                graph_id=graph.id,
                node_uid=str(uuid.uuid4()),
                local_id=node_spec.id,
                depends_on_json=json.dumps(node_spec.depends_on, ensure_ascii=False),
                order_index=index,
                name=node_spec.name or node_spec.id,
                target=node_spec.target,
                instruction=node_spec.instruction,
                tools_json=json.dumps(node_spec.tools, ensure_ascii=False),
                status="pending",
            )
        )
    await db.commit()
    await db.refresh(graph)
    logger.info(
        "提交任务图草稿 graph_uid=%s session_id=%s title=%s nodes=%s",
        graph_uid,
        session_id,
        spec.title,
        len(spec.nodes),
    )
    return graph


async def update_spec(
    db: AsyncSession, graph_uid: str, spec: TaskGraphSpec
) -> bool:
    """按用户意见调整图（整图替换），仅草稿态可改。"""

    graph = await _get_graph(db, graph_uid)
    if graph is None or graph.status != "draft":
        return False
    existing = await _nodes_of(db, graph.id)
    for node in existing:
        await db.delete(node)
    await db.flush()

    graph.title = spec.title[:256]
    graph.question = spec.question
    graph.spec_json = spec.model_dump_json()
    for index, node_spec in enumerate(spec.nodes):
        import uuid

        db.add(
            TaskGraphNode(
                graph_id=graph.id,
                node_uid=str(uuid.uuid4()),
                local_id=node_spec.id,
                depends_on_json=json.dumps(node_spec.depends_on, ensure_ascii=False),
                order_index=index,
                name=node_spec.name or node_spec.id,
                target=node_spec.target,
                instruction=node_spec.instruction,
                tools_json=json.dumps(node_spec.tools, ensure_ascii=False),
                status="pending",
            )
        )
    await db.commit()
    logger.info("调整任务图草稿 graph_uid=%s nodes=%s", graph_uid, len(spec.nodes))
    return True


async def confirm_and_run(db: AsyncSession, graph_uid: str) -> bool:
    """确认草稿并开始执行；从草稿态或暂停态恢复。"""

    graph = await _get_graph(db, graph_uid)
    if graph is None or graph.status not in ("draft", "paused"):
        return False
    graph.status = "running"
    graph.started_at = graph.started_at or datetime.now(timezone.utc)
    graph.error = ""
    await db.commit()
    session_id = graph.session_id
    logger.info("确认执行任务图 graph_uid=%s session_id=%s", graph_uid, session_id)
    _spawn(_run_graph(graph_uid, session_id))
    return True


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------------------
# 图调度
# ---------------------------------------------------------------------------


async def _run_graph(graph_uid: str, session_id: str) -> None:
    """一次图运行：建工作流运行、注入 task_runner、执行 DAG。"""

    publish = task_graph_events.open(graph_uid)
    runtime = _ensure_runtime(graph_uid)
    runtime.running = True
    runtime.stop_requested = False
    await publish({"event": "graph_started", "graph_uid": graph_uid})
    try:
        from app.services.workflow import workflow_service

        async with SessionLocal() as db:
            graph = await _get_graph(db, graph_uid)
            if graph is None:
                return
            nodes = await _nodes_of(db, graph.id)
            if runtime.stop_requested:
                await _finish_graph(db, graph, nodes, status="stopped", publish=publish)
                return
            pending = [n for n in nodes if n.status in ("pending", "queued")]
            if not pending:
                await _finish_graph(db, graph, nodes, status="succeeded", publish=publish)
                return

            spec = TaskGraphSpec.model_validate_json(graph.spec_json or "{}")
            definition = graph_builder.build_definition(spec)
            budget = _budget_of(graph)
            node_uid_by_local = {n.local_id: n.node_uid for n in nodes}

            run = await workflow_service.create_system_run(
                db,
                workflow_name=SYSTEM_WORKFLOW_TASK_GRAPH,
                definition=definition,
                template=graph_builder.display_template(),
                trigger=RUN_TRIGGER_TASK_GRAPH,
                triggered_by=f"session:{session_id}",
                payload={"graph_uid": graph_uid},
            )
            run_uid = run.run_uid

        async def task_runner(node: Any) -> dict[str, Any]:
            node_uid = node_uid_by_local.get(node.id)
            if node_uid is None:
                raise RuntimeError(f"任务节点没有对应的节点行: {node.id}")
            return await execute_node(
                graph_uid=graph_uid,
                node_uid=node_uid,
                budget=budget,
                publish=publish,
                runtime=runtime,
            )

        try:
            async with SessionLocal() as db:
                run_row = await workflow_service.get_run(db, run_uid)
                await workflow_service.execute_existing_run(
                    db, run=run_row, extra_tools={"task_runner": task_runner}
                )
        except Exception:
            logger.exception("任务图编排执行失败 graph_uid=%s run_uid=%s", graph_uid, run_uid)
            async with SessionLocal() as db:
                graph = await _get_graph(db, graph_uid)
                if graph is not None:
                    nodes = await _nodes_of(db, graph.id)
                    for node in nodes:
                        if node.status == "running":
                            node.status = "failed"
                            node.error = "编排执行中断"
                            node.finished_at = datetime.now(timezone.utc)
                    await db.commit()

        # 正常执行完毕（或异常兜底后）统一聚合并收尾，确保终态与汇总写回对话流。
        async with SessionLocal() as db:
            await _refresh_graph_state(db, graph_uid, publish)
    finally:
        runtime.running = False


# ---------------------------------------------------------------------------
# 状态聚合与收尾
# ---------------------------------------------------------------------------


async def _refresh_graph_state(
    db: AsyncSession, graph_uid: str, publish: Any
) -> dict[str, Any]:
    """锁内聚合并发布 graph_updated；全部终态时收尾汇总。"""

    async with _refresh_lock(graph_uid):
        graph = await _get_graph(db, graph_uid)
        if graph is None:
            return {}
        nodes = await _nodes_of(db, graph.id)
        runtime = _runtimes.get(graph_uid)

        # 停止请求：把所有未终态节点收口为 stopped，避免图悬在 running。
        if runtime is not None and runtime.stop_requested:
            for node in nodes:
                if node.status not in _TERMINAL_NODE:
                    node.status = "stopped"
                    node.finished_at = datetime.now(timezone.utc)
            await db.commit()

        statuses = [n.status for n in nodes]
        running = any(s not in _TERMINAL_NODE for s in statuses)

        if running:
            graph.status = "running"
        elif runtime is not None and runtime.stop_requested:
            graph.status = "stopped"
        elif all(s == "succeeded" for s in statuses):
            graph.status = "succeeded"
        elif any(s == "succeeded" for s in statuses):
            graph.status = "succeeded"  # 部分成功按成功收口，失败留在节点行
        else:
            graph.status = "failed"

        if not running:
            graph.finished_at = datetime.now(timezone.utc)
            graph.duration_ms = sum(n.duration_ms for n in nodes)
        await db.commit()

        stats = _stats_of(nodes)
        await publish(
            {
                "event": "graph_updated",
                "graph_uid": graph_uid,
                "status": graph.status,
                "stats": stats,
            }
        )
        should_finish = not running

    if should_finish:
        await _finish_graph(db, graph, nodes, status=graph.status, publish=publish)
    return {"status": graph.status, "stats": stats}


def _stats_of(nodes: list[TaskGraphNode]) -> dict[str, int]:
    result = {key: 0 for key in ("total", "succeeded", "failed", "skipped", "stopped", "running", "pending")}
    for node in nodes:
        status = node.status
        if status in result:
            result[status] += 1
    result["total"] = len(nodes)
    result["tool_calls"] = sum(
        len(
            [
                line
                for line in (n.log_text or "").splitlines()
                if line.startswith("调用工具 ")
            ]
        )
        for n in nodes
    )
    return result


async def _finish_graph(
    db: AsyncSession,
    graph: TaskGraphRun,
    nodes: list[TaskGraphNode],
    *,
    status: str,
    publish: Any,
) -> None:
    """图收尾：未终态节点补 stopped，写终态事件 + 带工具的收尾汇总。"""

    async with _refresh_lock(graph.graph_uid):
        for node in nodes:
            if node.status not in _TERMINAL_NODE:
                node.status = "stopped" if status == "stopped" else node.status
        graph.status = status
        graph.finished_at = datetime.now(timezone.utc)
        graph.duration_ms = sum(n.duration_ms for n in nodes)
        await db.commit()

    await publish(
        {"event": "graph_updated", "graph_uid": graph.graph_uid, "status": status}
    )

    from app.services.execution.agent_assembly import assemble_agent
    from app.services.taskgraph.graph_summary import run_graph_summary

    summary_nodes = [
        {
            "name": n.name,
            "conclusion": n.summary or n.error or "无结论",
            "findings": json.loads(n.findings_json or "[]"),
            "tool_trace": json.loads(n.tool_trace_json or "[]"),
        }
        for n in nodes
    ]
    budget = _budget_of(graph)
    header = f"任务图执行已全部完成 · {len(nodes)} 个节点收口"
    summary_message_id = await run_graph_summary(
        db,
        session_id=graph.session_id,
        graph_uid=graph.graph_uid,
        header=header,
        question=graph.question,
        nodes=summary_nodes,
        budget=budget,
        assemble_agent_fn=assemble_agent,
        publish=publish,
    )
    await publish(
        {
            "event": "graph_finished",
            "graph_uid": graph.graph_uid,
            "status": status,
            "summary_message_id": summary_message_id,
            "stats": _stats_of(nodes),
        }
    )
    logger.info(
        "任务图收尾完成 graph_uid=%s status=%s nodes=%s summary=%s",
        graph.graph_uid,
        status,
        len(nodes),
        summary_message_id,
    )


# ---------------------------------------------------------------------------
# 控制：停止 / 重试 / 跳过
# ---------------------------------------------------------------------------


async def stop_graph(db: AsyncSession, graph_uid: str) -> bool:
    """停止：请求所有运行中节点停下；无运行时直接落 stopped。"""

    graph = await _get_graph(db, graph_uid)
    if graph is None or graph.status in _TERMINAL_GRAPH:
        return False
    runtime = _ensure_runtime(graph_uid)
    any_active = False
    for node_uid in runtime.node_uids:
        if runtime.stop.request_stop(node_uid):
            any_active = True
    runtime.stop_requested = True
    if not runtime.running:
        nodes = await _nodes_of(db, graph.id)
        await _finish_graph(
            db, graph, nodes, status="stopped", publish=task_graph_events.open(graph_uid)
        )
    else:
        await _refresh_graph_state(db, graph_uid, task_graph_events.open(graph_uid))
    logger.info("停止任务图 graph_uid=%s active=%s", graph_uid, any_active)
    return True


async def retry_node(db: AsyncSession, graph_uid: str, node_uid: str) -> TaskGraphNode | None:
    """重试失败/停止节点：attempt+1、状态回 pending，重新跑图。"""

    node = await _get_node(db, node_uid)
    graph = await _get_graph(db, graph_uid)
    if node is None or graph is None or node.graph_id != graph.id:
        return None
    if node.status not in ("failed", "stopped"):
        return None
    node.attempt += 1
    node.status = "pending"
    node.error = ""
    node.log_text = ""
    node.findings_json = "[]"
    node.tool_trace_json = "[]"
    node.finished_at = None
    graph.status = "running"
    graph.finished_at = None
    await db.commit()

    runtime = _ensure_runtime(graph_uid)
    if node_uid not in runtime.node_uids:
        runtime.node_uids.append(node_uid)
    runtime.stop.begin(node_uid)
    session_id = graph.session_id
    logger.info("重试任务图节点 graph_uid=%s node_uid=%s attempt=%s", graph_uid, node_uid, node.attempt)
    _spawn(_run_graph(graph_uid, session_id))
    return node


async def skip_node(db: AsyncSession, graph_uid: str, node_uid: str) -> TaskGraphNode | None:
    """跳过失败/停止节点：标记 skipped，后继从缺口上下文继续。"""

    node = await _get_node(db, node_uid)
    graph = await _get_graph(db, graph_uid)
    if node is None or graph is None or node.graph_id != graph.id:
        return None
    if node.status not in ("failed", "stopped"):
        return None
    node.status = "skipped"
    node.summary = "（已跳过，结论缺口待人工补充）"
    node.error = ""
    node.finished_at = datetime.now(timezone.utc)
    graph.status = "running"
    graph.finished_at = None
    await db.commit()
    logger.info("跳过任务图节点 graph_uid=%s node_uid=%s", graph_uid, node_uid)
    # 跳过不改节点图的调度（后继已在本次运行中执行）；若图已终态则重新聚合收尾。
    runtime = _runtimes.get(graph_uid)
    if runtime is not None and not runtime.running:
        await _refresh_graph_state(db, graph_uid, task_graph_events.open(graph_uid))
    return node


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------


async def _get_graph(db: AsyncSession, graph_uid: str) -> TaskGraphRun | None:
    return (await db.scalars(select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid))).first()


async def _get_node(db: AsyncSession, node_uid: str) -> TaskGraphNode | None:
    return (await db.scalars(select(TaskGraphNode).where(TaskGraphNode.node_uid == node_uid))).first()


async def _nodes_of(db: AsyncSession, graph_id: int) -> list[TaskGraphNode]:
    return list(
        (
            await db.scalars(
                select(TaskGraphNode)
                .where(TaskGraphNode.graph_id == graph_id)
                .order_by(TaskGraphNode.order_index)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 快照 / wire（供工具与 API 共用）
# ---------------------------------------------------------------------------


def node_to_wire(row: TaskGraphNode) -> dict[str, Any]:
    """单节点 wire：状态、结论、证据、轨迹、用量、时间。"""

    return {
        "node_uid": row.node_uid,
        "local_id": row.local_id,
        "depends_on": json.loads(row.depends_on_json or "[]"),
        "order_index": row.order_index,
        "name": row.name,
        "target": row.target,
        "instruction": row.instruction,
        "tools": json.loads(row.tools_json or "[]"),
        "status": row.status,
        "attempt": row.attempt,
        "summary": row.summary,
        "findings": json.loads(row.findings_json or "[]"),
        "tool_trace": json.loads(row.tool_trace_json or "[]"),
        "log_text": row.log_text,
        "usage": json.loads(row.usage_json or "{}"),
        "error": row.error,
        "duration_ms": row.duration_ms,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


async def graph_to_wire(db: AsyncSession, graph: TaskGraphRun) -> dict[str, Any]:
    """完整图 wire：节点列表 + 聚合统计 + 全局发现。"""

    nodes = await _nodes_of(db, graph.id)
    used_tokens = sum(
        int(json.loads(n.usage_json or "{}").get("total_tokens") or 0) for n in nodes
    )
    return {
        "id": graph.id,
        "graph_uid": graph.graph_uid,
        "session_id": graph.session_id,
        "card_message_id": graph.card_message_id,
        "title": graph.title,
        "question": graph.question,
        "status": graph.status,
        "config": json.loads(graph.config_json or "{}"),
        "stats": _stats_of(nodes),
        "used_tokens": used_tokens,
        "nodes": [node_to_wire(n) for n in nodes],
        "findings": json.loads(graph.findings_json or "[]"),
        "error": graph.error,
        "duration_ms": graph.duration_ms,
        "created_by": graph.created_by,
        "created_at": graph.created_at.isoformat() if graph.created_at else None,
        "started_at": graph.started_at.isoformat() if graph.started_at else None,
        "finished_at": graph.finished_at.isoformat() if graph.finished_at else None,
    }


async def get_graph_row(db: AsyncSession, graph_uid: str) -> TaskGraphRun | None:
    return await _get_graph(db, graph_uid)


async def get_graph_wire(db: AsyncSession, graph_uid: str) -> dict[str, Any] | None:
    graph = await _get_graph(db, graph_uid)
    if graph is None:
        return None
    return await graph_to_wire(db, graph)


async def list_graphs(
    db: AsyncSession,
    *,
    session_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """按 id 倒序列图，可按会话过滤。"""

    from sqlalchemy import func

    where = []
    if session_id is not None:
        where.append(TaskGraphRun.session_id == session_id)
    total = (
        await db.scalars(
            select(func.count(TaskGraphRun.id)).where(*where)
        )
    ).one()
    rows = (
        await db.scalars(
            select(TaskGraphRun)
            .where(*where)
            .order_by(TaskGraphRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [await graph_to_wire(db, r) for r in rows], int(total)


async def tool_result_wire(db: AsyncSession, graph_uid: str) -> dict[str, Any]:
    """工具返回句柄：图标识、标题、状态、节点摘要、统计；不含结论正文。"""

    graph = await _get_graph(db, graph_uid)
    if graph is None:
        raise LookupError(f"任务图不存在: {graph_uid}")
    nodes = await _nodes_of(db, graph.id)
    return {
        "graph_uid": graph.graph_uid,
        "title": graph.title,
        "status": graph.status,
        "nodes": [
            {
                "node_uid": n.node_uid,
                "local_id": n.local_id,
                "name": n.name,
                "depends_on": json.loads(n.depends_on_json or "[]"),
                "status": n.status,
                "summary": n.summary,
                "error": n.error,
            }
            for n in nodes
        ],
        "stats": _stats_of(nodes),
    }
