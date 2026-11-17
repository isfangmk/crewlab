"""规划执行编排：计划 CRUD、顺序链图执行、失败恢复与 wire 转换。

口径：
  - 计划先落 draft（create_plan 工具产出可编辑计划），用户确认后 start 逐步执行；
  - 编排归属工作流：一段执行 = 一次顺序链图运行（pending 步骤各一个任务节点），
    运行记录落在工作流运行里；
  - 步骤严格按 order_index 顺序执行（非并行），前置步骤结论进下一步上下文；
  - 单步失败即暂停（paused）等用户决定：重试此步（增量续跑）/ 跳过并继续 / 停止；
  - 事件走 investigation_events 总线（与排查共用），plan_finished 收尾。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.headless import WorkerBudget
from app.adk_runtime.web_search_tool import build_session_web_search_tool
from app.db import SessionLocal
from app.models.chat import ChatSession
from app.models.plan import PlanExecution, PlanStep
from app.services.investigation.investigation_events import investigation_events
from app.services.investigation.worker_runner import build_worker_agent
from app.services.plan import plan_artifacts
from app.services.plan.step_runner import (
    PLAN_STEP_READONLY_TOOL_NAMES,
    build_plan_step_persona,
    build_plan_step_prompt,
    run_plan_step,
)
from app.services.platform.task_control import StopSignal

logger = logging.getLogger(__name__)

# 后台任务强引用：防 GC 回收（仿 eval_runner 的任务持有模式）
_tasks: set[asyncio.Task] = set()

# 步骤数量边界：太少不值得开计划，太多执行面拉太长
_MIN_STEPS = 1
_MAX_STEPS = 12

# 步骤终态集合
_TERMINAL_STEP = ("succeeded", "failed", "skipped", "stopped")

# 计划终态集合
_TERMINAL_PLAN = ("succeeded", "stopped")

# plan_finished 是事件总线的收尾事件（与排查的 dispatch_finished 各自收尾）
PLAN_TERMINAL_EVENTS = ("plan_finished",)


@dataclass
class _PlanRuntime:
    """一次计划执行的进程内运行态（落库仍是权威，这里只管协调）。"""

    plan_uid: str
    stop: StopSignal
    # 用户请求过暂停：当前步骤协作停止，计划转 paused 等继续/重试/跳过
    pause_requested: bool = False
    # 用户请求过停止：全部转终态
    stop_requested: bool = False
    # 后台执行循环是否在跑（防止重复起循环）
    running: bool = False


# 进程内运行态注册表：结束后保留到 GC，停止/暂停都从这取
_runtimes: dict[str, _PlanRuntime] = {}

# 聚合刷新锁：并发的步骤落库与用户操作同时聚合会互相覆写状态（丢失更新）
_refresh_locks: dict[str, asyncio.Lock] = {}


def _refresh_lock(plan_uid: str) -> asyncio.Lock:
    lock = _refresh_locks.get(plan_uid)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[plan_uid] = lock
    return lock


def _ensure_runtime(plan_uid: str) -> _PlanRuntime:
    runtime = _runtimes.get(plan_uid)
    if runtime is None:
        runtime = _PlanRuntime(
            plan_uid=plan_uid, stop=StopSignal(f"plan:{plan_uid}")
        )
        _runtimes[plan_uid] = runtime
    return runtime


def parse_steps_json(steps_json: str) -> list[dict[str, Any]]:
    """校验并规整步骤规格：[{name, instruction, tools?}]。"""

    try:
        raw = json.loads(steps_json)
    except ValueError as exc:
        raise ValueError(f"steps_json 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("steps_json 必须是数组")
    if not (_MIN_STEPS <= len(raw) <= _MAX_STEPS):
        raise ValueError(f"步骤数量必须在 {_MIN_STEPS}..{_MAX_STEPS}")
    specs: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"steps[{i}] 必须是对象")
        name = str(item.get("name") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if name == "":
            raise ValueError(f"steps[{i}].name 不能为空")
        tools_raw = item.get("tools") or []
        if not isinstance(tools_raw, list):
            raise ValueError(f"steps[{i}].tools 必须是数组")
        tools = [str(t).strip() for t in tools_raw if str(t).strip()]
        specs.append(
            {
                "name": name[:128],
                "instruction": instruction,
                "tools": tools[:8],
            }
        )
    return specs


async def create_plan(
    db: AsyncSession,
    *,
    session_id: str,
    title: str,
    question: str,
    steps: list[dict[str, Any]],
    budget: WorkerBudget,
    created_by: int | None,
) -> PlanExecution:
    """建计划与步骤行（draft 态，等待用户确认后执行）。"""

    plan_uid = str(uuid.uuid4())
    plan = PlanExecution(
        plan_uid=plan_uid,
        session_id=session_id,
        title=title[:256],
        question=question,
        status="draft",
        config_json=json.dumps(
            {
                "max_steps_per_step": budget.max_steps,
                "timeout_seconds_per_step": budget.timeout_seconds,
                "token_budget_per_step": budget.token_budget,
            },
            ensure_ascii=False,
        ),
        created_by=created_by,
    )
    db.add(plan)
    await db.flush()
    for i, spec in enumerate(steps):
        db.add(
            PlanStep(
                plan_id=plan.id,
                step_uid=str(uuid.uuid4()),
                order_index=i,
                name=spec["name"],
                instruction=spec.get("instruction", ""),
                tools_hint=json.dumps(spec.get("tools", []), ensure_ascii=False),
                status="pending",
            )
        )
    await db.commit()
    await db.refresh(plan)
    return plan


async def update_steps(
    db: AsyncSession, plan_uid: str, steps: list[dict[str, Any]]
) -> bool:
    """整体替换步骤（排序/增删/改文案）；仅 draft 态可改。"""

    plan = await _get_plan(db, plan_uid)
    if plan is None or plan.status != "draft":
        return False
    rows = await _steps_of(db, plan.id)
    for row in rows:
        await db.delete(row)
    await db.flush()
    for i, spec in enumerate(steps):
        db.add(
            PlanStep(
                plan_id=plan.id,
                step_uid=str(uuid.uuid4()),
                order_index=i,
                name=spec["name"],
                instruction=spec.get("instruction", ""),
                tools_hint=json.dumps(spec.get("tools", []), ensure_ascii=False),
                status="pending",
            )
        )
    await db.commit()
    return True


async def start_plan(db: AsyncSession, plan_uid: str) -> bool:
    """确认执行：draft/paused → running，起顺序执行循环。"""

    plan = await _get_plan(db, plan_uid)
    if plan is None or plan.status not in ("draft", "paused"):
        return False
    plan.status = "running"
    plan.started_at = plan.started_at or datetime.now(timezone.utc)
    plan.error = ""
    await db.commit()
    _spawn_runner(plan_uid)
    return True


async def pause_plan(plan_uid: str) -> bool:
    """请求暂停：当前步骤协作停止，计划转 paused 等后续决定。"""

    runtime = _runtimes.get(plan_uid)
    if runtime is None or not runtime.running:
        return False
    runtime.pause_requested = True
    return True


async def stop_plan(db: AsyncSession, plan_uid: str) -> bool:
    """请求停止：当前与未执行步骤全部转 stopped。"""

    runtime = _ensure_runtime(plan_uid)
    plan = await _get_plan(db, plan_uid)
    if plan is None or plan.status in _TERMINAL_PLAN:
        return False
    runtime.stop_requested = True
    if not runtime.running:
        # 循环不在跑（draft/paused）：直接落终态，收口与运行路径同一函数（含自动汇总）
        rows = await _steps_of(db, plan.id)
        publish = investigation_events.open(
            plan_uid, terminal_events=PLAN_TERMINAL_EVENTS
        )
        await _finish_plan(db, plan, rows, status="stopped", publish=publish)
    return True


async def retry_step(db: AsyncSession, plan_uid: str, step_uid: str) -> bool:
    """重试失败/停止的一步：原地递增 attempt，恢复执行循环。"""

    plan = await _get_plan(db, plan_uid)
    step = await _get_step(db, step_uid)
    if plan is None or step is None or step.plan_id != plan.id:
        return False
    if step.status not in ("failed", "stopped"):
        return False
    step.attempt += 1
    step.status = "pending"
    step.error = ""
    plan.status = "running"
    plan.finished_at = None
    await db.commit()
    _spawn_runner(plan_uid)
    return True


async def skip_step(db: AsyncSession, plan_uid: str, step_uid: str) -> bool:
    """跳过失败/停止的一步：写占位摘要并继续，汇总时提示缺口。"""

    plan = await _get_plan(db, plan_uid)
    step = await _get_step(db, step_uid)
    if plan is None or step is None or step.plan_id != plan.id:
        return False
    if step.status not in ("failed", "stopped"):
        return False
    step.status = "skipped"
    step.summary = "（已跳过，结论缺口待人工补充）"
    step.error = ""
    plan.status = "running"
    plan.finished_at = None
    await db.commit()
    _spawn_runner(plan_uid)
    return True


def _spawn_runner(plan_uid: str) -> None:
    """起一段顺序链图执行（同一计划同一时刻只跑一段）。"""

    runtime = _ensure_runtime(plan_uid)
    # 新一段从干净信号开始：暂停/停止只作用于发起它们的那一段，
    # 否则 paused 恢复的第一段会被上一段的暂停信号拦回去
    runtime.pause_requested = False
    runtime.stop_requested = False
    if runtime.running:
        return
    runtime.running = True
    task = asyncio.create_task(_run_plan_graph(plan_uid))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _mark_paused(db: AsyncSession, plan: PlanExecution, publish: Any) -> None:
    """计划转 paused 等用户决定：状态落库 + 事件。"""

    async with _refresh_lock(plan.plan_uid):
        plan.status = "paused"
        await db.commit()
    await publish(
        {"event": "plan_updated", "plan_uid": plan.plan_uid, "status": "paused"}
    )


async def _run_plan_graph(plan_uid: str) -> None:
    """一段顺序链图执行：pending 步骤各一个任务节点，节点入口做状态机闸。

    一段 = 一次工作流运行；单步失败即暂停（下一步骤的入口闸收口本段），
    恢复（重试/跳过/继续）时按剩余 pending 步骤生成新的一段。
    """

    publish = investigation_events.open(plan_uid, terminal_events=PLAN_TERMINAL_EVENTS)
    runtime = _ensure_runtime(plan_uid)
    await publish({"event": "plan_started", "plan_uid": plan_uid})
    try:
        from app.services.workflow import workflow_service
        from app.services.workflow.system_templates import plan_definition, plan_template

        async with SessionLocal() as db:
            plan = await _get_plan(db, plan_uid)
            if plan is None:
                return
            rows = await _steps_of(db, plan.id)
            # 段前闸：与原执行循环的顶部检查同口径
            if runtime.stop_requested:
                await _finish_plan(db, plan, rows, status="stopped", publish=publish)
                return
            if runtime.pause_requested or any(r.status == "failed" for r in rows):
                await _mark_paused(db, plan, publish)
                return
            pending = [r for r in rows if r.status in ("pending", "queued")]
            if not pending:
                await _finish_plan(db, plan, rows, status="succeeded", publish=publish)
                return
            budget = _budget_of(plan)
            step_uid_by_node = {
                f"step_{i}": r.step_uid for i, r in enumerate(pending, start=1)
            }
            definition = plan_definition(
                [
                    {
                        "name": r.name,
                        "target": r.name,
                        "instruction": r.instruction,
                        "tools": json.loads(r.tools_hint or "[]"),
                    }
                    for r in pending
                ]
            )
            run = await workflow_service.create_system_run(
                db,
                workflow_name=workflow_service.SYSTEM_WORKFLOW_PLAN,
                definition=definition,
                template=plan_template(),
                trigger=workflow_service.RUN_TRIGGER_PLAN,
                triggered_by=f"session:{plan.session_id}",
                payload={"plan_uid": plan_uid},
            )

        async def step_runner(node: Any) -> dict[str, Any]:
            step_uid = step_uid_by_node.get(node.id)
            if step_uid is None:
                raise RuntimeError(f"任务节点没有对应的步骤行: {node.id}")
            return await _execute_step_node(
                plan_uid=plan_uid,
                step_uid=step_uid,
                budget=budget,
                publish=publish,
                runtime=runtime,
            )

        try:
            async with SessionLocal() as db:
                run_row = await workflow_service.get_run(db, run.run_uid)
                await workflow_service.execute_existing_run(
                    db, run=run_row, extra_tools={"task_runner": step_runner}
                )
        except Exception:
            logger.exception(
                "计划编排执行失败 plan_uid=%s run_uid=%s", plan_uid, run.run_uid
            )
            # 编排中断兜底：running 步骤退回 pending 并转 paused，不让计划悬死
            try:
                async with SessionLocal() as db:
                    plan = await _get_plan(db, plan_uid)
                    if plan is None:
                        return
                    rows = await _steps_of(db, plan.id)
                    for row in rows:
                        if row.status == "running":
                            row.status = "pending"
                    await db.commit()
                    await _mark_paused(db, plan, publish)
            except Exception:
                logger.exception("计划编排中断兜底失败 plan_uid=%s", plan_uid)
    finally:
        runtime.running = False


async def _execute_step_node(
    *,
    plan_uid: str,
    step_uid: str,
    budget: WorkerBudget,
    publish: Any,
    runtime: _PlanRuntime,
) -> dict[str, Any]:
    """单步任务节点：入口状态机闸 → 执行 → 落库 → 段尾收口检查。"""

    from app.adk_runtime.workflow_nodes import WorkflowStopped

    # —— 入口闸：暂停/停止/失败都在这里收口本段，不再往下走 ——
    async with SessionLocal() as db:
        plan = await _get_plan(db, plan_uid)
        if plan is None:
            raise WorkflowStopped("计划不存在")
        rows = await _steps_of(db, plan.id)
        if runtime.stop_requested:
            await _finish_plan(db, plan, rows, status="stopped", publish=publish)
            raise WorkflowStopped("计划已停止")
        if runtime.pause_requested or any(r.status == "failed" for r in rows):
            await _mark_paused(db, plan, publish)
            raise WorkflowStopped("计划暂停，等待用户决定")
        step = next((r for r in rows if r.step_uid == step_uid), None)
        if step is None or step.status not in ("pending", "queued"):
            # 段外操作已改掉这一步（如段内被跳过）：空转通过，不改状态
            return {
                "status": step.status if step is not None else "missing",
                "noop": True,
            }
        step.status = "running"
        # 记录开始时刻：前端「已运行」读秒的锚点
        step.started_at = datetime.now(timezone.utc)
        await db.commit()
        prior = [
            {
                "name": r.name,
                "conclusion": r.summary or "",
                "findings": json.loads(r.findings_json or "[]"),
                "tool_trace": json.loads(r.tool_trace_json or "[]"),
            }
            for r in rows
            if r.status == "succeeded" and r.summary
        ]
        prompt = build_plan_step_prompt(
            question=plan.question,
            name=step.name,
            instruction=step.instruction,
            prior_summaries=prior,
        )
        session_id = plan.session_id
        step_name = step.name
        step_instruction = step.instruction
        step_order = step.order_index
        # 声明了所需工具就收窄该步挂载面（tools 从展示升为治理输入）
        step_tools = tuple(json.loads(step.tools_hint or "[]"))
        await publish(
            {
                "event": "step_started",
                "plan_uid": plan_uid,
                "step_uid": step_uid,
                "name": step_name,
                "order_index": step_order,
            }
        )

    runtime.stop.begin(step_uid)

    # 默认参数即时绑定本步 step_uid，避免闭包看错步骤
    def _stop_check(uid: str = step_uid) -> bool:
        return (
            runtime.stop.is_stopped(uid)
            or runtime.stop_requested
            or runtime.pause_requested
        )

    turn_error = ""
    turn = None
    try:
        agent = await _assemble_step(
            session_id=session_id,
            name=step_name,
            instruction=step_instruction,
            budget=budget,
            tools=step_tools,
        )

        # 步骤内 run_python 的产出物即时落 artifacts 表（挂真实会话），
        # 否则只在 sandbox_executions、前端产出物面板看不到。
        step_message_id = f"plan-step-{step_uid}"

        async def _on_tool_response(call_id: str, name: str, response: Any) -> None:
            async with SessionLocal() as artifact_db:
                await plan_artifacts.persist_tool_response(
                    artifact_db,
                    session_id=session_id,
                    message_id=step_message_id,
                    call_id=call_id,
                    name=name,
                    response=response,
                )
                await artifact_db.commit()

        turn = await run_plan_step(
            agent=agent,
            plan_uid=plan_uid,
            step_uid=step_uid,
            name=step_name,
            prompt=prompt,
            budget=budget,
            publish=publish,
            stop_check=_stop_check,
            session_id=session_id,
            on_tool_response=_on_tool_response,
        )
    except Exception as exc:  # noqa: BLE001 - 装配失败也记成单步失败
        logger.exception("计划步骤装配失败 step_uid=%s", step_uid)
        turn_error = str(exc)
    finally:
        runtime.stop.end(step_uid)

    async with _refresh_lock(plan_uid):
        async with SessionLocal() as db:
            step = await _get_step(db, step_uid)
            if step is not None:
                if turn is not None:
                    step.log_text = turn.log_text
                    step.usage_json = json.dumps(turn.usage, ensure_ascii=False)
                    step.duration_ms = turn.duration_ms
                    step.tool_trace_json = json.dumps(turn.tool_trace, ensure_ascii=False)
                    if turn.status == "succeeded":
                        step.status = "succeeded"
                        step.summary = turn.conclusion
                        step.findings_json = json.dumps(
                            turn.findings, ensure_ascii=False
                        )
                        step.finished_at = datetime.now(timezone.utc)
                    elif turn.status == "stopped":
                        # 暂停打断的步骤退回待执行；停止打断的记 stopped
                        step.status = (
                            "stopped" if runtime.stop_requested else "pending"
                        )
                        step.log_text = turn.log_text
                    else:
                        step.status = "failed"
                        step.error = turn.error
                        step.finished_at = datetime.now(timezone.utc)
                else:
                    step.status = "failed"
                    step.error = turn_error
                    step.finished_at = datetime.now(timezone.utc)
                await db.commit()
            status = step.status if step is not None else "failed"
            error = step.error if step is not None else turn_error
            summary = step.summary if step is not None else ""
            duration_ms = step.duration_ms if step is not None else 0
    await publish(
        {
            "event": "step_finished",
            "plan_uid": plan_uid,
            "step_uid": step_uid,
            "status": status,
            "summary": summary,
            "error": error,
            "duration_ms": duration_ms,
        }
    )

    # —— 段尾收口：全部步骤终态时收尾（成功收口 / 失败暂停）——
    async with SessionLocal() as db:
        plan = await _get_plan(db, plan_uid)
        if plan is None:
            return {"status": status}
        rows = await _steps_of(db, plan.id)
        failed = any(r.status == "failed" for r in rows)
        if all(r.status in _TERMINAL_STEP for r in rows):
            if failed:
                await _mark_paused(db, plan, publish)
            else:
                await _finish_plan(db, plan, rows, status="succeeded", publish=publish)
        elif runtime.stop_requested:
            await _finish_plan(db, plan, rows, status="stopped", publish=publish)
        elif runtime.pause_requested or failed:
            # 本段最后一步被打断（暂停留下 pending）：转 paused 等决定
            await _mark_paused(db, plan, publish)
    return {"status": status, "summary": summary}


async def _finish_plan(
    db: AsyncSession,
    plan: PlanExecution,
    rows: list[PlanStep],
    *,
    status: str,
    publish: Any,
) -> None:
    """计划收尾：未执行步骤补终态，发收尾事件。"""

    async with _refresh_lock(plan.plan_uid):
        for row in rows:
            if row.status not in _TERMINAL_STEP:
                row.status = "stopped" if status == "stopped" else row.status
        plan.status = status
        plan.finished_at = datetime.now(timezone.utc)
        await db.commit()
    await publish(
        {"event": "plan_updated", "plan_uid": plan.plan_uid, "status": status}
    )
    # 全部收口：带工具的收尾汇总写回对话流（结论含具体数据 + 内联图表）
    from app.services.plan.plan_summary import run_plan_summary

    # 汇总与步骤共用同一份证据视图：findings + tool_trace + conclusion
    steps = [
        {
            "name": r.name,
            "conclusion": r.summary or r.error or "无结论",
            "findings": json.loads(r.findings_json or "[]"),
            "tool_trace": json.loads(r.tool_trace_json or "[]"),
        }
        for r in rows
    ]
    budget = _budget_of(plan)
    header = f"计划执行已全部完成 · {len(rows)} 步收口"
    summary_message_id = await run_plan_summary(
        db,
        session_id=plan.session_id,
        plan_uid=plan.plan_uid,
        header=header,
        question=plan.question,
        steps=steps,
        budget=budget,
        assemble_step=_assemble_step,
        publish=publish,
    )
    await publish(
        {
            "event": "plan_finished",
            "plan_uid": plan.plan_uid,
            "status": status,
            "summary_message_id": summary_message_id,
        }
    )


async def _assemble_step(
    *,
    session_id: str,
    name: str,
    instruction: str,
    budget: WorkerBudget,
    tools: tuple[str, ...] = (),
    persona: str | None = None,
):
    """按会话助理配置装配单步 agent（只读工具 + 继承 MCP + 禁写闸门）。

    persona 覆盖默认步骤人设（收尾汇总复用同款装配，只换人设）。
    """

    from app.services.agent import agent_service
    from app.services.agent.agent_tool_service import build_agent_tool_bundle
    from app.services.knowledge.knowledge_scope import KnowledgeScope
    from app.services.platform import app_settings_service, llm_config
    from app.services.platform.llm_config import resolve_model

    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            raise LookupError(f"会话不存在: {session_id}")
        if not session.agent_id:
            raise LookupError("会话未绑定 agent_id")
        resolved_agent = await agent_service.get_agent(db, session.agent_id)
        use_model = session.model
        if resolved_agent.model != "":
            use_model = resolved_agent.model
        llm = await resolve_model(db, session.provider, use_model)
        bundle = await build_agent_tool_bundle(db, resolved_agent)
        runtime_cfg = await app_settings_service.get_agent_runtime(db)
        scope = (
            resolved_agent.knowledge_scope
            if isinstance(resolved_agent.knowledge_scope, KnowledgeScope)
            else None
        )
        extra_tools = list(bundle.extra_tools)
        web_tool = await build_session_web_search_tool(db, session)
        if web_tool is not None:
            extra_tools.append(web_tool)
        return build_worker_agent(
            provider_id=llm.provider_id,
            model=use_model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            name=name,
            target=name,
            instruction="",
            extra_tools=extra_tools,
            # 技能不进步骤（口径同 worker：只读内置 + 知识库 + 继承 MCP）
            skills=[],
            knowledge_scope=scope,
            model_prefix=llm.model_prefix,
            provider_extra_body=llm_config.provider_extra_body(llm),
            disallowed_tool_names=bundle.disallowed_tool_names,
            budget=budget,
            llm_timeout_seconds=int(runtime_cfg["llm_timeout_seconds"]),
            persona=persona if persona is not None else build_plan_step_persona(name, instruction),
            readonly_tools=PLAN_STEP_READONLY_TOOL_NAMES,
            # 声明了所需工具就收窄挂载面，不声明挂默认全量只读集
            tool_focus=tools or None,
        )


def _budget_of(plan: PlanExecution) -> WorkerBudget:
    config = json.loads(plan.config_json or "{}")
    # 缺省取 WorkerBudget()：预算默认值只有 headless 一个源头
    defaults = WorkerBudget()
    return WorkerBudget(
        max_steps=int(config.get("max_steps_per_step") or defaults.max_steps),
        timeout_seconds=int(config.get("timeout_seconds_per_step") or defaults.timeout_seconds),
        token_budget=int(config.get("token_budget_per_step") or defaults.token_budget),
    )


async def _get_plan(db: AsyncSession, plan_uid: str) -> PlanExecution | None:
    return (
        await db.scalars(select(PlanExecution).where(PlanExecution.plan_uid == plan_uid))
    ).first()


async def _get_step(db: AsyncSession, step_uid: str) -> PlanStep | None:
    return (await db.scalars(select(PlanStep).where(PlanStep.step_uid == step_uid))).first()


async def _steps_of(db: AsyncSession, plan_id: int) -> list[PlanStep]:
    rows = (
        await db.scalars(
            select(PlanStep)
            .where(PlanStep.plan_id == plan_id)
            .order_by(PlanStep.order_index)
        )
    ).all()
    return list(rows)


def _step_to_wire(row: PlanStep) -> dict[str, Any]:
    return {
        "step_uid": row.step_uid,
        "order_index": row.order_index,
        "name": row.name,
        "instruction": row.instruction,
        "tools": json.loads(row.tools_hint or "[]"),
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


def _stats_of(rows: list[PlanStep]) -> dict[str, Any]:
    tool_calls = sum(
        1 for r in rows for line in r.log_text.splitlines() if line.startswith("调用工具 ")
    )
    return {
        "total": len(rows),
        "succeeded": sum(1 for r in rows if r.status == "succeeded"),
        "failed": sum(1 for r in rows if r.status == "failed"),
        "skipped": sum(1 for r in rows if r.status == "skipped"),
        "stopped": sum(1 for r in rows if r.status == "stopped"),
        "running": sum(1 for r in rows if r.status == "running"),
        "pending": sum(1 for r in rows if r.status in ("pending", "queued")),
        "tool_calls": tool_calls,
    }


async def plan_to_wire(db: AsyncSession, plan: PlanExecution) -> dict[str, Any]:
    """计划快照 wire：步骤按序、统计与用量。"""

    rows = await _steps_of(db, plan.id)
    usage_total = sum(
        int(json.loads(r.usage_json or "{}").get("total_tokens") or 0) for r in rows
    )
    return {
        "id": plan.id,
        "plan_uid": plan.plan_uid,
        "session_id": plan.session_id,
        "title": plan.title,
        "question": plan.question,
        "status": plan.status,
        "config": json.loads(plan.config_json or "{}"),
        "stats": _stats_of(rows),
        "used_tokens": usage_total,
        "steps": [_step_to_wire(r) for r in rows],
        "error": plan.error,
        "duration_ms": plan.duration_ms,
        "created_by": plan.created_by,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "started_at": plan.started_at.isoformat() if plan.started_at else None,
        "finished_at": plan.finished_at.isoformat() if plan.finished_at else None,
    }


async def get_plan_wire(db: AsyncSession, plan_uid: str) -> dict[str, Any] | None:
    plan = await _get_plan(db, plan_uid)
    if plan is None:
        return None
    return await plan_to_wire(db, plan)


async def list_plans(
    db: AsyncSession, *, session_id: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """计划列表（按创建时间倒序）。"""

    query = select(PlanExecution)
    count_query = select(func.count()).select_from(PlanExecution)
    if session_id:
        query = query.where(PlanExecution.session_id == session_id)
        count_query = count_query.where(PlanExecution.session_id == session_id)
    total = int((await db.execute(count_query)).scalar_one())
    rows = (
        (
            await db.scalars(
                query.order_by(PlanExecution.id.desc()).limit(limit).offset(offset)
            )
        )
        .all()
    )
    return [await plan_to_wire(db, r) for r in rows], total


async def tool_result_wire(db: AsyncSession, plan_uid: str) -> dict[str, Any]:
    """create_plan / get_plan 的工具返回：计划与步骤摘要。"""

    wire = await get_plan_wire(db, plan_uid)
    if wire is None:
        raise LookupError(f"计划不存在: {plan_uid}")
    return {
        "plan_uid": wire["plan_uid"],
        "title": wire["title"],
        "status": wire["status"],
        "steps": [
            {
                "step_uid": s["step_uid"],
                "name": s["name"],
                "status": s["status"],
                "summary": s["summary"],
                "error": s["error"],
            }
            for s in wire["steps"]
        ],
        "stats": wire["stats"],
    }
