"""并行排查编排：派发 N 路 worker、软截止返回、重试、停止与 wire 转换。

口径：
  - 软截止：工具等全部 worker 终态或到期即返回；未完的路后台续跑，
    进度继续走事件总线（investigation_events），右栏可看；
  - findings 全局编号且不复用：重试路的新发现追加新号，旧号保留 retired 标记；
  - 后台任务持强引用防 GC 回收（仿 eval_runner）；
  - 单路失败不拖垮整次排查，状态记在 worker 行上。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.adk_runtime.headless import WorkerBudget
from app.adk_runtime.web_search_tool import build_session_web_search_tool
from app.db import SessionLocal
from app.models.chat import ChatSession
from app.models.investigation import InvestigationDispatch, InvestigationWorkerRun
from app.services.investigation.investigation_events import investigation_events
from app.services.investigation.worker_runner import (
    WorkerResult,
    build_worker_agent,
    run_worker,
)
from app.services.platform.task_control import StopSignal

logger = logging.getLogger(__name__)

# 后台任务强引用：防 GC 回收（仿 eval_runner 的任务持有模式）
_tasks: set[asyncio.Task] = set()

# worker 数量边界：太少没意义，太多白烧 token
_MIN_WORKERS = 1
_MAX_WORKERS = 8

# worker 终态集合
_TERMINAL_WORKER = ("succeeded", "failed", "stopped")


@dataclass
class _DispatchRuntime:
    """一次排查的进程内运行态（落库仍是权威，这里只管协调）。"""

    dispatch_uid: str
    stop: StopSignal
    worker_uids: list[str] = field(default_factory=list)
    # 工具已软截止返回（findings 不全但已出初步汇总）
    returned_partial: bool = False
    # 用户请求过停止
    stop_requested: bool = False
    all_done: asyncio.Event = field(default_factory=asyncio.Event)


# 进程内运行态注册表：排查结束后保留到 GC，工具等待与停止都从这取
_runtimes: dict[str, _DispatchRuntime] = {}

# 派发聚合刷新锁：并发的 worker 落库与停止请求同时聚合会互相覆写状态（丢失更新）
_refresh_locks: dict[str, asyncio.Lock] = {}


def _refresh_lock(dispatch_uid: str) -> asyncio.Lock:
    lock = _refresh_locks.get(dispatch_uid)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[dispatch_uid] = lock
    return lock


def parse_workers_json(workers_json: str) -> list[dict[str, Any]]:
    """校验并规整 worker 规格：[{name, target, instruction, tools?}]。"""

    try:
        raw = json.loads(workers_json)
    except ValueError as exc:
        raise ValueError(f"workers_json 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("workers_json 必须是数组")
    if not (_MIN_WORKERS <= len(raw) <= _MAX_WORKERS):
        raise ValueError(f"worker 数量必须在 {_MIN_WORKERS}..{_MAX_WORKERS}")
    specs: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"workers[{i}] 必须是对象")
        name = str(item.get("name") or "").strip()
        target = str(item.get("target") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if name == "" or target == "":
            raise ValueError(f"workers[{i}].name/target 不能为空")
        tools_raw = item.get("tools") or []
        if not isinstance(tools_raw, list):
            raise ValueError(f"workers[{i}].tools 必须是数组")
        tools = [str(t).strip() for t in tools_raw if str(t).strip()]
        specs.append(
            {
                "name": name[:128],
                "target": target,
                "instruction": instruction,
                "tools": tools[:8],
            }
        )
    return specs


async def start_dispatch(
    db,
    *,
    session_id: str,
    question: str,
    workers: list[dict[str, Any]],
    budget: WorkerBudget,
    created_by: int | None,
) -> InvestigationDispatch:
    """建派发记录与 worker 行，并并行起 N 路后台执行。"""

    dispatch_uid = str(uuid.uuid4())
    dispatch = InvestigationDispatch(
        dispatch_uid=dispatch_uid,
        session_id=session_id,
        question=question,
        status="running",
        config_json=json.dumps(
            {
                "workers": workers,
                "max_steps_per_worker": budget.max_steps,
                "timeout_seconds_per_worker": budget.timeout_seconds,
                "token_budget_per_worker": budget.token_budget,
            },
            ensure_ascii=False,
        ),
        created_by=created_by,
    )
    db.add(dispatch)
    await db.flush()

    runtime = _DispatchRuntime(dispatch_uid=dispatch_uid, stop=StopSignal(f"dispatch:{dispatch_uid}"))
    _runtimes[dispatch_uid] = runtime

    for spec in workers:
        worker_uid = str(uuid.uuid4())
        runtime.worker_uids.append(worker_uid)
        runtime.stop.begin(worker_uid)
        db.add(
            InvestigationWorkerRun(
                dispatch_id=dispatch.id,
                worker_uid=worker_uid,
                name=spec["name"],
                target=spec["target"],
                status="queued",
                budget_json=json.dumps(
                    {
                        "token_budget": budget.token_budget,
                        "max_steps": budget.max_steps,
                        "timeout_seconds": budget.timeout_seconds,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    await db.commit()
    await db.refresh(dispatch)

    for worker_uid, spec in zip(runtime.worker_uids, workers):
        task = asyncio.create_task(
            _worker_task(
                dispatch_uid=dispatch_uid,
                worker_uid=worker_uid,
                session_id=session_id,
                question=question,
                spec=spec,
                budget=budget,
            )
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return dispatch


async def wait_dispatch(dispatch_uid: str, soft_deadline_seconds: int) -> bool:
    """等全部 worker 终态或软截止到期。

    返回 True 表示全部终态（完整结果）；False 表示到期（初步结果，后台续跑）。
    """

    runtime = _runtimes.get(dispatch_uid)
    if runtime is None:
        return True
    try:
        await asyncio.wait_for(
            runtime.all_done.wait(),
            timeout=soft_deadline_seconds if soft_deadline_seconds > 0 else None,
        )
        return True
    except asyncio.TimeoutError:
        runtime.returned_partial = True
        async with SessionLocal() as db:
            await _refresh_dispatch_state(db, dispatch_uid)
        return False


def is_partial(dispatch_uid: str) -> bool:
    """软截止后仍有 worker 在后台续跑。"""

    runtime = _runtimes.get(dispatch_uid)
    return bool(runtime and runtime.returned_partial and not runtime.all_done.is_set())


async def stop_dispatch(db, dispatch_uid: str) -> bool:
    """全部停止：置协作停止标记，跑着的 worker 在下一个事件间隙退出。

    返回 True 表示确有可停止的 worker。
    """

    runtime = _runtimes.get(dispatch_uid)
    if runtime is None:
        return False
    any_active = False
    for worker_uid in runtime.worker_uids:
        if runtime.stop.request_stop(worker_uid):
            any_active = True
    if any_active:
        runtime.stop_requested = True
        await _refresh_dispatch_state(db, dispatch_uid)
    return any_active


async def retry_worker(db, dispatch_uid: str, worker_uid: str) -> InvestigationWorkerRun | None:
    """重试失败/停止的一路：原地递增 attempt，发现重新编号（旧号转 retired）。"""

    row = await _get_worker(db, dispatch_uid, worker_uid)
    if row is None:
        return None
    if row.status not in ("failed", "stopped"):
        raise ValueError("只能重试失败或已停止的 worker")
    dispatch = await db.get(InvestigationDispatch, row.dispatch_id)
    if dispatch is None:
        return None

    row.attempt += 1
    row.status = "queued"
    row.error = ""
    row.log_text = ""
    row.finished_at = None
    await _merge_findings(dispatch, worker_uid, None)
    # 重试回到进行中：终态先撤销，跑完再重新聚合
    dispatch.status = "running"
    dispatch.finished_at = None
    await db.commit()

    runtime = _runtimes.get(dispatch_uid)
    if runtime is None:
        runtime = _DispatchRuntime(dispatch_uid=dispatch_uid, stop=StopSignal(f"dispatch:{dispatch_uid}"))
        _runtimes[dispatch_uid] = runtime
    if worker_uid not in runtime.worker_uids:
        runtime.worker_uids.append(worker_uid)
    runtime.stop.begin(worker_uid)
    runtime.returned_partial = False
    runtime.all_done.clear()

    config = json.loads(dispatch.config_json or "{}")
    # 缺省取 WorkerBudget()：预算默认值只有 headless 一个源头
    defaults = WorkerBudget()
    budget = WorkerBudget(
        max_steps=int(config.get("max_steps_per_worker") or defaults.max_steps),
        timeout_seconds=int(config.get("timeout_seconds_per_worker") or defaults.timeout_seconds),
        token_budget=int(config.get("token_budget_per_worker") or defaults.token_budget),
    )
    spec = next(
        (w for w in config.get("workers", []) if w.get("name") == row.name),
        {"name": row.name, "target": row.target, "instruction": ""},
    )
    task = asyncio.create_task(
        _worker_task(
            dispatch_uid=dispatch_uid,
            worker_uid=worker_uid,
            session_id=dispatch.session_id,
            question=dispatch.question,
            spec=spec,
            budget=budget,
        )
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    await db.refresh(row)
    return row


async def _worker_task(
    *,
    dispatch_uid: str,
    worker_uid: str,
    session_id: str,
    question: str,
    spec: dict[str, str],
    budget: WorkerBudget,
) -> None:
    """单路 worker 的后台执行：装配 → 运行 → 落库 → 聚合派发状态。"""

    publish = investigation_events.open(dispatch_uid)
    runtime = _runtimes.get(dispatch_uid)
    stop = runtime.stop if runtime is not None else StopSignal(f"dispatch:{dispatch_uid}")
    # worker_started 在任务入口发：装配失败也要让右栏看到「已开始」
    await publish(
        {
            "event": "worker_started",
            "dispatch_uid": dispatch_uid,
            "worker_uid": worker_uid,
            "name": spec["name"],
            "target": spec["target"],
        }
    )
    try:
        agent = await _assemble_worker(
            session_id=session_id,
            spec=spec,
            budget=budget,
        )
        result = await run_worker(
            agent=agent,
            dispatch_uid=dispatch_uid,
            worker_uid=worker_uid,
            name=spec["name"],
            target=spec["target"],
            question=question,
            instruction=spec.get("instruction", ""),
            budget=budget,
            publish=publish,
            stop_check=lambda: stop.is_stopped(worker_uid),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 装配失败也记成单路失败
        logger.exception("worker 装配失败 worker_uid=%s", worker_uid)
        result = WorkerResult(worker_uid=worker_uid, status="failed", error=str(exc))
    stop.end(worker_uid)
    try:
        async with SessionLocal() as db:
            await _persist_worker_result(db, dispatch_uid, result)
    except Exception:  # noqa: BLE001
        logger.exception("worker 结果落库失败 worker_uid=%s", worker_uid)


async def _assemble_worker(
    *, session_id: str, spec: dict[str, Any], budget: WorkerBudget
):
    """按会话助理配置装配单路 worker（只读工具 + 继承 MCP + 禁写闸门）。"""

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
            raise LookupError(f"会话未绑定 agent_id: {session_id}")
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
        # 联网搜索只在对话开启 web_search 且凭证可用时挂载给 worker
        extra_tools = list(bundle.extra_tools)
        web_tool = await build_session_web_search_tool(db, session)
        if web_tool is not None:
            extra_tools.append(web_tool)
        return build_worker_agent(
            provider_id=llm.provider_id,
            model=use_model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            name=spec["name"],
            target=spec["target"],
            instruction=spec.get("instruction", ""),
            extra_tools=extra_tools,
            # 技能不进 worker（口径：只读内置 + 知识库 + 继承 MCP）：
            # load_skill 展开后的声明工具会与既有工具撞名（Tool names must be unique）
            skills=[],
            knowledge_scope=scope,
            model_prefix=llm.model_prefix,
            provider_extra_body=llm_config.provider_extra_body(llm),
            disallowed_tool_names=bundle.disallowed_tool_names,
            budget=budget,
            llm_timeout_seconds=int(runtime_cfg["llm_timeout_seconds"]),
            # 声明了所需工具就收窄挂载面，不声明挂默认全量只读集
            tool_focus=tuple(spec.get("tools") or ()) or None,
        )


async def _persist_worker_result(db, dispatch_uid: str, result: WorkerResult) -> None:
    """worker 结果落库，并触发派发状态聚合。"""

    row = (
        await db.scalars(
            select(InvestigationWorkerRun).where(
                InvestigationWorkerRun.worker_uid == result.worker_uid
            )
        )
    ).first()
    if row is None:
        logger.warning("worker 行不存在，丢弃结果 worker_uid=%s", result.worker_uid)
        return
    dispatch = await db.get(InvestigationDispatch, row.dispatch_id)
    if dispatch is None or dispatch.dispatch_uid != dispatch_uid:
        logger.warning("dispatch 不匹配，丢弃结果 dispatch_uid=%s", dispatch_uid)
        return

    row.status = result.status
    row.error = result.error
    row.log_text = result.log_text
    row.duration_ms = result.duration_ms
    row.finished_at = datetime.now(timezone.utc)
    row.findings_json = json.dumps(
        {"conclusion": result.conclusion, "findings": result.findings},
        ensure_ascii=False,
    )
    budget = json.loads(row.budget_json or "{}")
    if result.usage:
        budget["used_tokens"] = result.usage
    row.budget_json = json.dumps(budget, ensure_ascii=False)

    await _merge_findings(dispatch, result.worker_uid, result.findings)
    await db.commit()

    publish = investigation_events.open(dispatch_uid)
    await publish(
        {
            "event": "worker_findings",
            "dispatch_uid": dispatch_uid,
            "worker_uid": result.worker_uid,
            "conclusion": result.conclusion,
            "findings": result.findings,
        }
    )
    await publish(
        {
            "event": "worker_finished",
            "dispatch_uid": dispatch_uid,
            "worker_uid": result.worker_uid,
            "status": result.status,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "usage": result.usage,
        }
    )
    await _refresh_dispatch_state(db, dispatch_uid)


async def _merge_findings(dispatch: InvestigationDispatch, worker_uid: str, new_findings: list[dict] | None) -> None:
    """合并本路发现到全局编号序列：旧发现转 retired，新发现追加新号（不复用）。"""

    existing = json.loads(dispatch.findings_json or "[]")
    if new_findings is None:
        # 重试清空：本路旧发现全部转 retired
        for item in existing:
            if item.get("worker_uid") == worker_uid:
                item["retired"] = True
        dispatch.findings_json = json.dumps(existing, ensure_ascii=False)
        return
    for item in existing:
        if item.get("worker_uid") == worker_uid:
            item["retired"] = True
    next_n = max((int(item.get("n") or 0) for item in existing), default=0) + 1
    for finding in new_findings:
        existing.append(
            {
                "n": next_n,
                "worker_uid": worker_uid,
                "retired": False,
                "title": finding.get("title", ""),
                "detail": finding.get("detail", ""),
                "evidence": finding.get("evidence", []),
            }
        )
        next_n += 1
    dispatch.findings_json = json.dumps(existing, ensure_ascii=False)


async def _refresh_dispatch_state(db, dispatch_uid: str) -> dict[str, Any]:
    """聚合 worker 状态 → 更新派发行 + 发事件；全部终态时收尾（按派发串行）。"""

    async with _refresh_lock(dispatch_uid):
        return await _refresh_dispatch_state_locked(db, dispatch_uid)


async def _refresh_dispatch_state_locked(db, dispatch_uid: str) -> dict[str, Any]:
    dispatch = (
        await db.scalars(
            select(InvestigationDispatch).where(
                InvestigationDispatch.dispatch_uid == dispatch_uid
            )
        )
    ).first()
    if dispatch is None:
        return {}
    rows = (
        (
            await db.scalars(
                select(InvestigationWorkerRun)
                .where(InvestigationWorkerRun.dispatch_id == dispatch.id)
                .order_by(InvestigationWorkerRun.id)
            )
        )
        .all()
    )
    runtime = _runtimes.get(dispatch_uid)
    statuses = [r.status for r in rows]
    running = [r for r in rows if r.status not in _TERMINAL_WORKER]

    if running:
        dispatch.status = "partial" if (runtime and runtime.returned_partial) else "running"
        if runtime:
            runtime.all_done.clear()
    else:
        if runtime and runtime.stop_requested:
            dispatch.status = "stopped"
        elif all(s == "succeeded" for s in statuses):
            dispatch.status = "succeeded"
        elif any(s == "succeeded" for s in statuses):
            dispatch.status = "succeeded"  # 部分成功：发现可用，失败路在 worker 行上
        else:
            dispatch.status = "failed"
        dispatch.finished_at = datetime.now(timezone.utc)
        dispatch.duration_ms = sum(r.duration_ms for r in rows)

    # 预算聚合：配置 + 已用；缺键不造假 0
    config = json.loads(dispatch.config_json or "{}")
    used_total = 0
    used_known = False
    for r in rows:
        b = json.loads(r.budget_json or "{}")
        used = b.get("used_tokens")
        if isinstance(used, dict):
            total = used.get("total_tokens")
            if isinstance(total, int):
                used_total += total
                used_known = True
    budget: dict[str, Any] = {
        "token_budget_per_worker": config.get("token_budget_per_worker"),
        "max_steps_per_worker": config.get("max_steps_per_worker"),
        "worker_count": len(rows),
    }
    if used_known:
        budget["used_tokens_total"] = used_total
    dispatch.budget_json = json.dumps(budget, ensure_ascii=False)
    await db.commit()
    # 全部终态：提交后再唤醒等待方，否则唤醒时状态还没落库（丢失更新的读端）
    if not running and runtime is not None:
        runtime.all_done.set()

    publish = investigation_events.open(dispatch_uid)
    stats = _stats_of(rows, dispatch)
    await publish(
        {"event": "dispatch_updated", "dispatch_uid": dispatch_uid, "status": dispatch.status, "stats": stats}
    )
    if not running:
        # 全部收口：自动汇总写回对话流，汇总随终态事件带 message_id 供前端取
        from app.services.chat.auto_summary import run_auto_summary

        items = [
            {
                "label": f"{r.name}（{r.target}）" if r.target else r.name,
                "status": r.status,
                "conclusion": (
                    json.loads(r.findings_json or "{}").get("conclusion")
                    or r.error
                    or "无结论"
                ),
            }
            for r in rows
        ]
        gaps = [f"{r.name}：{r.error}" for r in rows if r.status != "succeeded" and r.error]
        summary_message_id = await run_auto_summary(
            db,
            session_id=dispatch.session_id,
            kind="investigation",
            run_uid=dispatch_uid,
            header=f"后台排查已全部完成 · {len(rows)} 路收口",
            question=dispatch.question,
            items=items,
            gaps=gaps,
        )
        await publish(
            {
                "event": "dispatch_finished",
                "dispatch_uid": dispatch_uid,
                "status": dispatch.status,
                "stats": stats,
                "summary_message_id": summary_message_id,
            }
        )
    return {"status": dispatch.status, "stats": stats}


def _stats_of(rows: list[InvestigationWorkerRun], dispatch: InvestigationDispatch) -> dict[str, Any]:
    """派发统计：各状态路数、有效发现数、工具调用粗计。"""

    findings = json.loads(dispatch.findings_json or "[]")
    active = [f for f in findings if not f.get("retired")]
    evidence_count = sum(len(f.get("evidence") or []) for f in active)
    return {
        "total": len(rows),
        "succeeded": sum(1 for r in rows if r.status == "succeeded"),
        "failed": sum(1 for r in rows if r.status == "failed"),
        "stopped": sum(1 for r in rows if r.status == "stopped"),
        "running": sum(1 for r in rows if r.status not in _TERMINAL_WORKER),
        "findings": len(active),
        "tool_calls": evidence_count,
    }


async def _get_worker(db, dispatch_uid: str, worker_uid: str) -> InvestigationWorkerRun | None:
    return (
        await db.scalars(
            select(InvestigationWorkerRun)
            .join(InvestigationDispatch, InvestigationWorkerRun.dispatch_id == InvestigationDispatch.id)
            .where(
                InvestigationDispatch.dispatch_uid == dispatch_uid,
                InvestigationWorkerRun.worker_uid == worker_uid,
            )
        )
    ).first()


def _worker_to_wire(row: InvestigationWorkerRun) -> dict[str, Any]:
    payload = json.loads(row.findings_json or "{}")
    if not isinstance(payload, dict):
        # 未写过的空行兜底空对象
        payload = {}
    budget = json.loads(row.budget_json or "{}")
    return {
        "worker_uid": row.worker_uid,
        "name": row.name,
        "target": row.target,
        "attempt": row.attempt,
        "status": row.status,
        "conclusion": payload.get("conclusion", ""),
        "findings": payload.get("findings", []),
        "log_text": row.log_text,
        "error": row.error,
        "budget": budget,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


async def dispatch_to_wire(db, dispatch: InvestigationDispatch) -> dict[str, Any]:
    """派发快照 wire：含全局编号发现与统计。"""

    rows = (
        (
            await db.scalars(
                select(InvestigationWorkerRun)
                .where(InvestigationWorkerRun.dispatch_id == dispatch.id)
                .order_by(InvestigationWorkerRun.id)
            )
        )
        .all()
    )
    findings = json.loads(dispatch.findings_json or "[]")
    return {
        "id": dispatch.id,
        "dispatch_uid": dispatch.dispatch_uid,
        "session_id": dispatch.session_id,
        "question": dispatch.question,
        "status": dispatch.status,
        "config": json.loads(dispatch.config_json or "{}"),
        "budget": json.loads(dispatch.budget_json or "{}"),
        "findings": findings,
        "stats": _stats_of(rows, dispatch),
        "workers": [_worker_to_wire(r) for r in rows],
        "error": dispatch.error,
        "duration_ms": dispatch.duration_ms,
        "created_by": dispatch.created_by,
        "created_at": dispatch.created_at.isoformat() if dispatch.created_at else None,
        "finished_at": dispatch.finished_at.isoformat() if dispatch.finished_at else None,
    }


async def get_dispatch_wire(db, dispatch_uid: str) -> dict[str, Any] | None:
    dispatch = (
        await db.scalars(
            select(InvestigationDispatch).where(
                InvestigationDispatch.dispatch_uid == dispatch_uid
            )
        )
    ).first()
    if dispatch is None:
        return None
    return await dispatch_to_wire(db, dispatch)


async def list_dispatches(
    db, *, session_id: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """排查列表（按创建时间倒序）。"""

    query = select(InvestigationDispatch)
    count_query = select(func.count()).select_from(InvestigationDispatch)
    if session_id:
        query = query.where(InvestigationDispatch.session_id == session_id)
        count_query = count_query.where(InvestigationDispatch.session_id == session_id)
    total = int((await db.execute(count_query)).scalar_one())
    rows = (
        (
            await db.scalars(
                query.order_by(InvestigationDispatch.id.desc()).limit(limit).offset(offset)
            )
        )
        .all()
    )
    return [await dispatch_to_wire(db, r) for r in rows], total


async def tool_result_wire(db, dispatch_uid: str) -> dict[str, Any]:
    """dispatch_investigation 的工具返回：初步/完整结果 + 仍在跑的路。"""

    wire = await get_dispatch_wire(db, dispatch_uid)
    if wire is None:
        raise LookupError(f"排查不存在: {dispatch_uid}")
    still_running = [
        {"worker_uid": w["worker_uid"], "name": w["name"]}
        for w in wire["workers"]
        if w["status"] not in _TERMINAL_WORKER
    ]
    return {
        "dispatch_uid": dispatch_uid,
        "status": wire["status"],
        "partial": bool(still_running),
        "workers": [
            {
                "worker_uid": w["worker_uid"],
                "name": w["name"],
                "status": w["status"],
                "conclusion": w["conclusion"],
                "findings": [
                    f
                    for f in wire["findings"]
                    if f.get("worker_uid") == w["worker_uid"] and not f.get("retired")
                ],
                "error": w["error"],
                "duration_ms": w["duration_ms"],
            }
            for w in wire["workers"]
        ],
        "still_running": still_running,
        "findings": [f for f in wire["findings"] if not f.get("retired")],
    }
