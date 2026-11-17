"""任务图节点执行器：入口状态机闸 → 前驱上下文 → headless 执行 → 落库 → 事件。

由编排服务经 workflow task_runner 注入，每个任务节点调用一次。
节点失败不抛给引擎（引擎的无条件边会让后继照常起跑），failed 结果写回本节点，
后继在其上下文里看到失败前驱的缺口说明；只有停止/暂停抛 WorkflowStopped 收口整段。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent

from app.adk_runtime.headless import WorkerBudget
from app.db import SessionLocal
from app.models.task_graph import TaskGraphNode, TaskGraphRun
from app.services.execution.agent_assembly import assemble_agent
from app.services.execution.artifacts import persist_tool_response
from app.services.execution.context import render_prior_context
from app.services.execution.node_runner import build_node_prompt, run_node_turn

logger = logging.getLogger(__name__)

_TERMINAL_NODE = ("succeeded", "failed", "skipped", "stopped")


async def _get_graph(db: Any, graph_uid: str) -> TaskGraphRun | None:
    from sqlalchemy import select

    return (await db.scalars(select(TaskGraphRun).where(TaskGraphRun.graph_uid == graph_uid))).first()


async def _get_node(db: Any, node_uid: str) -> TaskGraphNode | None:
    from sqlalchemy import select

    return (await db.scalars(select(TaskGraphNode).where(TaskGraphNode.node_uid == node_uid))).first()


async def _nodes_of(db: Any, graph_id: int) -> list[TaskGraphNode]:
    from sqlalchemy import select

    return list(
        (
            await db.scalars(
                select(TaskGraphNode)
                .where(TaskGraphNode.graph_id == graph_id)
                .order_by(TaskGraphNode.order_index)
            )
        ).all()
    )


def _build_prior_context(
    node: TaskGraphNode, all_nodes: list[TaskGraphNode]
) -> list[str]:
    """按依赖取前驱结果渲染上下文；失败前驱以缺口形式纳入，不编造结论。"""

    depends_on = json.loads(node.depends_on_json or "[]")
    by_local = {n.local_id: n for n in all_nodes}
    prior: list[dict[str, Any]] = []
    gaps: list[str] = []
    for dep_local in depends_on:
        dep = by_local.get(dep_local)
        if dep is None:
            gaps.append(f"前驱节点 {dep_local} 不存在")
            continue
        if dep.status == "succeeded" and dep.summary:
            prior.append(
                {
                    "name": dep.name,
                    "conclusion": dep.summary,
                    "findings": json.loads(dep.findings_json or "[]"),
                    "tool_trace": json.loads(dep.tool_trace_json or "[]"),
                }
            )
        elif dep.status == "failed":
            gaps.append(f"前驱节点 {dep.name} 失败：{dep.error or '无错误信息'}")
        elif dep.status in ("skipped", "stopped"):
            gaps.append(f"前驱节点 {dep.name} 未产出结果（{dep.status}）")

    lines = render_prior_context(prior)
    if gaps:
        lines.append("前驱缺口（相关结论证据不足，请如实标注）：")
        lines.extend(f"     - {gap}" for gap in gaps)
    return lines


async def execute_node(
    *,
    graph_uid: str,
    node_uid: str,
    budget: WorkerBudget,
    publish: Callable[[dict[str, Any]], Awaitable[None]],
    runtime: Any,
) -> dict[str, Any]:
    """执行单个任务节点，返回给 workflow 引擎的结果 dict。"""

    from app.adk_runtime.workflow_nodes import WorkflowStopped

    # —— 入口闸 ——
    async with SessionLocal() as db:
        graph = await _get_graph(db, graph_uid)
        if graph is None:
            raise WorkflowStopped("任务图不存在")
        if runtime.stop_requested:
            raise WorkflowStopped("任务图已停止")
        node = await _get_node(db, node_uid)
        if node is None or node.status not in ("pending", "queued"):
            return {
                "status": node.status if node is not None else "missing",
                "noop": True,
            }
        all_nodes = await _nodes_of(db, graph.id)
        node.status = "running"
        node.started_at = datetime.now(timezone.utc)
        await db.commit()

        question = graph.question
        session_id = graph.session_id
        name = node.name
        target = node.target
        instruction = node.instruction
        tools = tuple(json.loads(node.tools_json or "[]"))
        prior_context = _build_prior_context(node, all_nodes)
        prompt = build_node_prompt(
            question=question,
            name=name,
            target=target,
            instruction=instruction,
            prior_context=prior_context,
        )
        await publish(
            {
                "event": "node_started",
                "graph_uid": graph_uid,
                "node_uid": node_uid,
                "name": name,
                "local_id": node.local_id,
                "depends_on": json.loads(node.depends_on_json or "[]"),
            }
        )

    runtime.stop.begin(node_uid)

    def _stop_check(uid: str = node_uid) -> bool:
        return (
            runtime.stop.is_stopped(uid)
            or runtime.stop_requested
            or runtime.pause_requested
        )

    turn_error = ""
    turn = None
    try:
        agent: LlmAgent = await assemble_agent(
            session_id=session_id,
            name=name,
            target=target,
            instruction=instruction,
            budget=budget,
            tools=tools,
            allow_run_python=True,
        )

        node_message_id = f"taskgraph-node-{node_uid}"

        async def _on_tool_response(call_id: str, tool_name: str, response: Any) -> None:
            async with SessionLocal() as artifact_db:
                await persist_tool_response(
                    artifact_db,
                    session_id=session_id,
                    message_id=node_message_id,
                    call_id=call_id,
                    name=tool_name,
                    response=response,
                )
                await artifact_db.commit()

        turn = await run_node_turn(
            agent=agent,
            graph_uid=graph_uid,
            node_uid=node_uid,
            name=name,
            prompt=prompt,
            budget=budget,
            publish=publish,
            stop_check=_stop_check,
            session_id=session_id,
            on_tool_response=_on_tool_response,
        )
    except Exception as exc:  # noqa: BLE001 - 装配失败也记成单节点失败
        logger.exception("任务图节点装配失败 node_uid=%s", node_uid)
        turn_error = str(exc)
    finally:
        runtime.stop.end(node_uid)

    # —— 落库 ——
    async with SessionLocal() as db:
        node = await _get_node(db, node_uid)
        if node is not None:
            if turn is not None:
                node.log_text = turn.log_text
                node.usage_json = json.dumps(turn.usage, ensure_ascii=False)
                node.duration_ms = turn.duration_ms
                node.tool_trace_json = json.dumps(turn.tool_trace, ensure_ascii=False)
                if turn.status == "succeeded":
                    node.status = "succeeded"
                    node.summary = turn.conclusion
                    node.findings_json = json.dumps(turn.findings, ensure_ascii=False)
                    node.finished_at = datetime.now(timezone.utc)
                elif turn.status == "stopped":
                    node.status = "stopped" if runtime.stop_requested else "pending"
                    node.log_text = turn.log_text
                else:
                    node.status = "failed"
                    node.error = turn.error
                    node.finished_at = datetime.now(timezone.utc)
            else:
                node.status = "failed"
                node.error = turn_error
                node.finished_at = datetime.now(timezone.utc)
            await db.commit()
        status = node.status if node is not None else "failed"
        error = node.error if node is not None else turn_error
        summary = node.summary if node is not None else ""
        duration_ms = node.duration_ms if node is not None else 0
        findings = json.loads(node.findings_json or "[]") if node is not None else []

    await publish(
        {
            "event": "node_finished",
            "graph_uid": graph_uid,
            "node_uid": node_uid,
            "name": name,
            "local_id": node.local_id if node else "",
            "status": status,
            "summary": summary,
            "error": error,
            "duration_ms": duration_ms,
        }
    )
    return {"status": status, "summary": summary, "findings": findings}
