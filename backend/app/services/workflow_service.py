"""工作流运行：装配 ADK 图、执行、落运行与节点记录。

审批 / 人工输入节点会让一次运行停在 waiting，运行服务负责在恢复时带上用户输入
继续执行；其余节点由 ADK 图执行器调度。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Sequence
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime import workflow_graph
from app.adk_runtime.workflow_nodes import RunContext, preview
from app.models_orm.workflow import Workflow as WorkflowRow
from app.models_orm.workflow import WorkflowNodeRun, WorkflowRun
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowNode,
    declared_variables,
    parse_definition,
)

logger = logging.getLogger(__name__)

WORKFLOW_APP_NAME = "workflow"

# 运行状态
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_WAITING = "waiting"

# 触发来源：manual 是正式运行，debug 是画布上的单节点试跑
RUN_TRIGGER_MANUAL = "manual"
RUN_TRIGGER_DEBUG = "debug"


def initial_state_of(
    definition: WorkflowDefinition, payload: dict[str, Any]
) -> dict[str, Any]:
    return initial_state(definition.require_start(), payload)


def initial_state(
    node: WorkflowNode, payload: dict[str, Any]
) -> dict[str, Any]:
    """按开始节点声明的变量整理初始状态。

    必填变量运行时必须由用户提供；非必填变量可回退到声明的默认值。
    """

    state: dict[str, Any] = {}
    for variable in node.variables:
        if variable.name in payload:
            raw = payload[variable.name]
        elif variable.default is not None:
            raw = variable.default
        elif variable.required:
            raise ValueError(f"运行输入缺少必填变量: {variable.name}")
        else:
            continue
        state[variable.name] = _coerce(raw, variable.type)
    return state


def _coerce(value: Any, type_name: str) -> Any:
    if type_name == "string":
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if type_name == "number":
        # 整数保持整数：3 被转成 3.0 会在插值文案里变成 "3.0"
        if isinstance(value, bool):
            raise ValueError(f"变量需要数字，收到布尔值: {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"变量需要数字，收到: {value!r}") from exc
    if type_name == "boolean":
        if isinstance(value, bool):
            return value
        raise ValueError(f"变量需要布尔值，收到: {value!r}")
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(f"变量需要 JSON，收到: {value!r}") from exc


def _first_message(values: dict[str, Any]) -> types.Content:
    """把运行输入文本化后作为 START 的 node_input（节点主要读 state）。"""

    return types.Content(
        role="user", parts=[types.Part(text=json.dumps(values, ensure_ascii=False))]
    )


async def execute_run(
    db: AsyncSession,
    *,
    workflow: WorkflowRow,
    payload: dict[str, Any],
    triggered_by: str,
) -> WorkflowRun:
    """创建运行记录并执行到结束 / 等待人工。"""

    definition = parse_definition(workflow.definition_json)
    run_uid = f"run_{uuid4().hex[:12]}"
    initial_state = initial_state_of(definition, payload)
    run = WorkflowRun(
        workflow_id=workflow.id,
        run_uid=run_uid,
        definition_json=workflow.definition_json,
        workflow_version=workflow.version,
        status=RUN_RUNNING,
        trigger=RUN_TRIGGER_MANUAL,
        triggered_by=triggered_by,
        input_json=json.dumps(initial_state, ensure_ascii=False),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    logger.info(
        "工作流开始运行 run_uid=%s workflow=%s version=%s nodes=%s state_keys=%s",
        run_uid,
        workflow.name,
        workflow.version,
        [n.id for n in definition.nodes],
        sorted(initial_state),
    )
    return await _drive(
        db, run=run, definition=definition, state=initial_state, resumed_state=None
    )


async def run_single_node(
    db: AsyncSession,
    *,
    workflow: WorkflowRow,
    definition: WorkflowDefinition,
    node_id: str,
    payload: dict[str, Any],
    triggered_by: str,
) -> dict[str, Any]:
    """单节点试跑：从开始节点跑到目标节点就收工，返回该节点这一轮的结果。

    试跑照常落运行记录（trigger=debug）：上游节点是真实执行的，它们的输出也要能
    在各自面板的「上次运行」里看到，否则点后面节点的运行按钮时，前面跑过的节点
    看起来像是没跑过。下游节点不会被写入，因为它们确实没执行。
    """

    target = _node_by_id(definition, node_id)
    if target.kind in ("approval", "input"):
        raise ValueError(f"人工节点不能试跑：需要在完整运行里等人工提交（{node_id}）")
    if target.kind == "start":
        raise ValueError("开始节点没有上游逻辑可试跑，直接运行工作流即可")

    initial = initial_state_of(definition, payload)
    run = WorkflowRun(
        workflow_id=workflow.id,
        run_uid=f"run_{uuid4().hex[:12]}",
        # 试跑跑的是请求里这份定义（可能还没保存），快照按它存，别存库里那份旧的
        definition_json=definition.model_dump_json(),
        workflow_version=workflow.version,
        status=RUN_RUNNING,
        trigger=RUN_TRIGGER_DEBUG,
        triggered_by=triggered_by,
        input_json=json.dumps(initial, ensure_ascii=False),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    started_at = time.monotonic()
    session_service = InMemorySessionService()
    assembly = None
    runner = None
    stopped = False
    try:
        assembly = await workflow_graph.assemble(
            db=db,
            definition=definition,
            run_values=initial,
            stop_after=node_id,
        )
        runner = Runner(
            app_name=WORKFLOW_APP_NAME,
            node=assembly.graph,
            session_service=session_service,
        )
        session = await session_service.create_session(
            app_name=WORKFLOW_APP_NAME, user_id=triggered_by
        )
        logger.info(
            "单节点试跑开始 run_uid=%s node=%s kind=%s state_keys=%s",
            run.run_uid,
            node_id,
            target.kind,
            sorted(initial),
        )
        async for event in runner.run_async(
            user_id=triggered_by,
            session_id=session.id,
            new_message=_first_message(initial),
        ):
            logger.info(
                "单节点试跑事件 run_uid=%s node=%s output_type=%s",
                run.run_uid,
                getattr(event, "node_name", None),
                type(event.output).__name__,
            )
    except Exception as exc:
        stopped = assembly is not None and assembly.run_ctx.stopped
        if not (stopped and _is_stop_after(exc)):
            # 先把已经跑过的节点落库，再决定要不要把这次失败抛给调用方：
            # 上游失败时面板要能看到是哪一步炸的。
            await _finish_debug_run(
                db,
                run=run,
                assembly=assembly,
                node_id=node_id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                fallback_error=f"{type(exc).__name__}: {exc}",
            )
            gate_id = None if assembly is None else assembly.run_ctx.gate_hit
            if gate_id is not None:
                raise ValueError(
                    f"运行会停在人工节点 {gate_id}，无法试跑 {node_id}"
                ) from exc
            outcomes = [] if assembly is None else assembly.run_ctx.outcomes
            # 目标节点自己失败：结果照常回给面板，由面板显示这一步的报错
            target_failed = next(
                (o for o in outcomes if o.node_id == node_id and o.status == "failed"),
                None,
            )
            if target_failed is not None:
                return {
                    "node_id": target_failed.node_id,
                    "node_kind": target_failed.node_kind,
                    "attempt": target_failed.attempt,
                    "status": target_failed.status,
                    "output": ""
                    if target_failed.output is None
                    else preview(target_failed.output),
                    "error": target_failed.error,
                    "duration_ms": target_failed.duration_ms,
                    "route": target_failed.route,
                }
            upstream_failed = [o for o in outcomes if o.status == "failed"]
            if upstream_failed:
                first = upstream_failed[0]
                raise ValueError(
                    f"上游节点 {first.node_id} 失败：{first.error}"
                ) from exc
            raise
    finally:
        if runner is not None:
            await runner.close()

    if assembly is None:
        raise RuntimeError(f"单节点试跑未完成装配: {node_id}")
    outcome = next(
        (o for o in assembly.run_ctx.outcomes if o.node_id == node_id), None
    )
    if outcome is None:
        await _finish_debug_run(
            db,
            run=run,
            assembly=assembly,
            node_id=node_id,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            fallback_error=f"节点未执行：{node_id}",
        )
        raise ValueError(f"节点未执行：{node_id}")

    await _finish_debug_run(
        db,
        run=run,
        assembly=assembly,
        node_id=node_id,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        fallback_error="",
    )
    logger.info(
        "单节点试跑结束 run_uid=%s node=%s status=%s duration_ms=%s stopped=%s",
        run.run_uid,
        node_id,
        outcome.status,
        outcome.duration_ms,
        stopped,
    )
    return {
        "node_id": outcome.node_id,
        "node_kind": outcome.node_kind,
        "attempt": outcome.attempt,
        "status": outcome.status,
        "output": "" if outcome.output is None else preview(outcome.output),
        "error": outcome.error,
        "duration_ms": outcome.duration_ms,
        "route": outcome.route,
    }


async def _finish_debug_run(
    db: AsyncSession,
    *,
    run: WorkflowRun,
    assembly: Any,
    node_id: str,
    duration_ms: int,
    fallback_error: str,
) -> None:
    """收尾一次试跑：状态、输出与已执行节点的记录一起落库。"""

    outcomes = [] if assembly is None else assembly.run_ctx.outcomes
    target = next((o for o in outcomes if o.node_id == node_id), None)
    run.status = RUN_FAILED if fallback_error != "" else RUN_SUCCEEDED
    run.duration_ms = duration_ms
    run.finished_at = datetime.now(UTC)
    if target is None:
        run.error = fallback_error
        run.output_json = ""
    else:
        run.error = target.error
        run.output_json = "" if target.output is None else preview(target.output)
        if target.status == "failed":
            run.status = RUN_FAILED
    await _replace_node_runs(db, run.id, outcomes)
    await db.commit()
    await db.refresh(run)


async def _replace_node_runs(
    db: AsyncSession, run_id: int, outcomes: Sequence[Any]
) -> None:
    """写入本轮执行过的节点记录。

    恢复运行时上一轮的记录已在库里：先删旧记录再写本轮，避免节点表出现重复行。
    """

    await db.execute(delete(WorkflowNodeRun).where(WorkflowNodeRun.run_id == run_id))
    for outcome in outcomes:
        db.add(
            WorkflowNodeRun(
                run_id=run_id,
                node_id=outcome.node_id,
                node_kind=outcome.node_kind,
                attempt=outcome.attempt,
                status=outcome.status,
                output_json="" if outcome.output is None else preview(outcome.output),
                error=outcome.error,
                duration_ms=outcome.duration_ms,
                route=outcome.route,
            )
        )


async def _drive(
    db: AsyncSession,
    *,
    run: WorkflowRun,
    definition: WorkflowDefinition,
    state: dict[str, Any],
    resumed_state: dict[str, Any] | None,
    approved: set[str] | None = None,
) -> WorkflowRun:
    """真正跑 ADK 图；把节点记录与最终状态落库。"""

    started_at = time.monotonic()
    status = RUN_SUCCEEDED
    error = ""
    output: Any = None
    assembly = None
    runner = None
    session_service = InMemorySessionService()
    # 人工节点恢复时，把之前的状态当成开始节点的输入重新注入
    run_values = dict(resumed_state) if resumed_state else dict(state)
    # 恢复时要带着已提交的人工输入与审批记录重跑，必须写回 input_json；
    # 否则下一次恢复会从最初的输入开始，丢掉中间几次提交的内容。
    run.input_json = json.dumps(run_values, ensure_ascii=False)

    try:
        assembly = await workflow_graph.assemble(
            db=db,
            definition=definition,
            run_values=run_values,
            # 恢复时已经确认过的人工节点不再拦路
            approved_gates=approved,
        )
        runner = Runner(
            app_name=WORKFLOW_APP_NAME,
            node=assembly.graph,
            session_service=session_service,
        )
        session = await session_service.create_session(
            app_name=WORKFLOW_APP_NAME, user_id=run.triggered_by
        )
        await _run_graph(
            runner=runner,
            session_id=session.id,
            run=run,
            run_values=run_values,
        )
        gate_id = assembly.run_ctx.gate_hit
        if gate_id is not None:
            gate = _node_by_id(definition, gate_id)
            status = RUN_WAITING
            raw = await _session_state(session_service, session.id, run.triggered_by)
            output = {
                "gate": gate.id,
                "kind": gate.kind,
                "prompt": _render_prompt(gate, raw),
            }
        else:
            raw = await _session_state(session_service, session.id, run.triggered_by)
            output = _collect_output(definition, raw)
    except Exception as exc:
        gate_id = None if assembly is None else assembly.run_ctx.gate_hit
        blocked = gate_id is not None
        if blocked and _is_gate_blocked(exc):
            # 停在人工节点：下游被拦下不是错误，运行进入等待
            gate = _node_by_id(definition, gate_id or "")
            status = RUN_WAITING
            output = {
                "gate": gate.id,
                "kind": gate.kind,
                "prompt": _render_prompt(gate, run_values),
            }
            logger.info(
                "运行停在人工节点 run_uid=%s node=%s kind=%s",
                run.run_uid,
                gate.id,
                gate.kind,
            )
        else:
            status = RUN_FAILED
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("工作流运行失败 run_uid=%s", run.run_uid)
    finally:
        if runner is not None:
            # Runner.close() 是唯一会关闭 toolset（MCP session manager）的路径
            await runner.close()

    duration_ms = int((time.monotonic() - started_at) * 1000)
    run.status = status
    run.error = error
    run.output_json = "" if output is None else preview(output)
    run.duration_ms = duration_ms
    if status in (RUN_SUCCEEDED, RUN_FAILED):
        run.finished_at = datetime.now(UTC)

    if assembly is not None:
        await _replace_node_runs(db, run.id, assembly.run_ctx.outcomes)
    await db.commit()
    await db.refresh(run)
    logger.info(
        "工作流运行结束 run_uid=%s status=%s duration_ms=%s nodes=%s",
        run.run_uid,
        status,
        duration_ms,
        [] if assembly is None else [(o.node_id, o.status, o.attempt) for o in assembly.run_ctx.outcomes],
    )
    return run


async def _run_graph(
    *,
    runner: Runner,
    session_id: str,
    run: WorkflowRun,
    run_values: dict[str, Any],
) -> None:
    """跑图；人工节点通过 RunContext.gate_hit 拦截。"""

    async for event in runner.run_async(
        user_id=run.triggered_by,
        session_id=session_id,
        new_message=_first_message(run_values),
    ):
        if event.output is not None:
            logger.info(
                "工作流事件 run_uid=%s node=%s output_type=%s",
                run.run_uid,
                getattr(event, "node_name", None),
                type(event.output).__name__,
            )


def _is_gate_blocked(exc: BaseException) -> bool:
    """判断中止是否来自「停在人工节点」，包括被 ADK 包装后的情况。"""

    from app.adk_runtime.workflow_nodes import WorkflowGateBlocked

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, WorkflowGateBlocked):
            return True
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        current = nested if isinstance(nested, BaseException) else None
    return "WorkflowGateBlocked" in repr(exc)


def _is_stop_after(exc: BaseException) -> bool:
    """判断中止是否来自「单节点试跑跑到目标就停」，同样要穿过 ADK 的包装。"""

    from app.adk_runtime.workflow_nodes import WorkflowStopAfter

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, WorkflowStopAfter):
            return True
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        current = nested if isinstance(nested, BaseException) else None
    return "WorkflowStopAfter" in repr(exc)


def _render_prompt(gate: WorkflowNode, state: dict[str, Any]) -> str:
    """人工节点文案支持 {{变量}}；变量缺失时退原文案并记日志，不让等待流程报错。"""

    from app.services import workflow_vars

    if gate.prompt == "":
        return ""
    try:
        return workflow_vars.render(gate.prompt, state)
    except workflow_vars.MissingVariableError as exc:
        logger.info(
            "人工节点文案变量未就绪 node=%s missing=%s", gate.id, exc.name
        )
        return gate.prompt


def _node_by_id(definition: WorkflowDefinition, node_id: str) -> WorkflowNode:
    for node in definition.nodes:
        if node.id == node_id:
            return node
    raise LookupError(f"节点不存在: {node_id}")


async def _session_state(
    service: InMemorySessionService, session_id: str, user_id: str
) -> dict[str, Any]:
    session = await service.get_session(
        app_name=WORKFLOW_APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise LookupError(f"工作流会话不存在: {session_id}")
    return dict(session.state)


def _collect_output(definition: WorkflowDefinition, state: dict[str, Any]) -> Any:
    """运行结果：有结束节点取它的 output_key，否则取整份状态（去掉内部键）。"""

    for node in definition.nodes:
        if node.kind == "end" and node.output_key != "":
            return state.get(node.output_key, {})
    return {
        key: value
        for key, value in sorted(state.items())
        if not key.startswith("__") and key != "text"
    }


async def list_runs(
    db: AsyncSession, *, workflow_id: int | None, limit: int, offset: int
) -> tuple[list[WorkflowRun], int]:
    stmt = select(WorkflowRun)
    count_stmt = select(func.count()).select_from(WorkflowRun)
    if workflow_id is not None:
        stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
        count_stmt = count_stmt.where(WorkflowRun.workflow_id == workflow_id)
    stmt = stmt.order_by(WorkflowRun.id.desc()).limit(limit).offset(offset)
    rows = list((await db.scalars(stmt)).all())
    total = await db.scalar(count_stmt)
    return rows, int(total or 0)


async def get_run(db: AsyncSession, run_uid: str) -> WorkflowRun:
    row = await db.scalar(select(WorkflowRun).where(WorkflowRun.run_uid == run_uid))
    if row is None:
        raise LookupError(f"运行记录不存在: {run_uid}")
    return row


async def list_node_runs(db: AsyncSession, run_id: int) -> list[WorkflowNodeRun]:
    stmt = (
        select(WorkflowNodeRun)
        .where(WorkflowNodeRun.run_id == run_id)
        .order_by(WorkflowNodeRun.id.asc())
    )
    return list((await db.scalars(stmt)).all())


def node_run_to_wire(row: WorkflowNodeRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "node_id": row.node_id,
        "node_kind": row.node_kind,
        "attempt": row.attempt,
        "status": row.status,
        "output": row.output_json,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "route": row.route,
        "created_at": row.created_at.isoformat(),
    }


async def resume_run(
    db: AsyncSession,
    *,
    run: WorkflowRun,
    decision: str,
    payload: dict[str, Any],
) -> WorkflowRun:
    """人工节点恢复：拒绝直接失败，同意/填写则带着输入继续跑完。"""

    if run.status != RUN_WAITING:
        raise ValueError(f"运行不在等待状态，无法恢复: {run.run_uid} status={run.status}")
    definition = parse_definition(run.definition_json)
    gate = _pending_gate(definition, run)
    if gate is None:
        raise ValueError(f"运行没有待处理的人工节点: {run.run_uid}")

    if decision == "reject":
        run.status = RUN_FAILED
        run.error = f"人工拒绝: {gate.id} {gate.prompt}"
        run.finished_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(run)
        logger.info("运行被人工拒绝 run_uid=%s node=%s", run.run_uid, gate.id)
        return run

    previous = json.loads(run.input_json)
    resumed = dict(previous)
    if gate.kind == "input":
        if gate.output_key == "":
            raise ValueError(f"人工输入节点必须设置 output_key: {gate.id}")
        if gate.output_key not in payload:
            raise ValueError(f"缺少提交字段: {gate.output_key}")
        resumed[gate.output_key] = payload[gate.output_key]
    # 已通过的节点记录在状态里，重跑时不再拦路
    approved = _approved_gates(run)
    approved.add(gate.id)
    resumed["__approved_gates"] = sorted(approved)
    logger.info(
        "运行恢复 run_uid=%s node=%s decision=%s fields=%s approved=%s",
        run.run_uid,
        gate.id,
        decision,
        sorted(payload),
        sorted(approved),
    )
    run.status = RUN_RUNNING
    await db.commit()
    return await _drive(
        db,
        run=run,
        definition=definition,
        state={},
        resumed_state=resumed,
        approved=approved,
    )


def _approved_gates(run: WorkflowRun) -> set[str]:
    raw = json.loads(run.input_json)
    names = raw.get("__approved_gates", [])
    if not isinstance(names, list):
        raise TypeError(f"运行输入里的 __approved_gates 必须是数组: {run.run_uid}")
    return {str(n) for n in names}


def _pending_gate(definition: WorkflowDefinition, run: WorkflowRun) -> WorkflowNode | None:
    """从运行的输出里读回停住的节点。"""

    if run.output_json == "":
        return None
    data = json.loads(run.output_json)
    if not isinstance(data, dict) or "gate" not in data:
        return None
    return _node_by_id(definition, str(data["gate"]))
