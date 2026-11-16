"""并行排查编排：扇出等全部收口（join）、发现编号、重试与停止。

执行缝（worker 的装配与 LLM 执行）打桩，编排图/落库/事件总线/编号/状态机
全走真实实现；用例自带专属 tag，finally 全清理（沿用 test_share_admin 口径）。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adk_runtime.headless import WorkerBudget, parse_findings
from app.config import get_settings
from app.db import create_db_engine
from app.models.chat import ChatMessage, ChatSession
from app.models.investigation import InvestigationDispatch, InvestigationWorkerRun
from app.models.workflow import WorkflowNodeRun, WorkflowRun
from app.services.investigation import dispatch_service
from app.services.investigation.worker_runner import (
    WorkerResult,
    build_worker_agent,
    build_worker_tool_gate,
    filter_tools_by_focus,
)


async def _cleanup(session_ids: list[str], dispatch_uids: list[str]) -> None:
    """插桩数据全清理：worker 行、派发行、系统编排运行、临时会话。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if dispatch_uids:
                rows = (
                    (
                        await db.scalars(
                            select(InvestigationDispatch).where(
                                InvestigationDispatch.dispatch_uid.in_(dispatch_uids)
                            )
                        )
                    )
                    .all()
                )
                ids = [r.id for r in rows]
                if ids:
                    await db.execute(
                        sa_delete(InvestigationWorkerRun).where(
                            InvestigationWorkerRun.dispatch_id.in_(ids)
                        )
                    )
                    await db.execute(
                        sa_delete(InvestigationDispatch).where(
                            InvestigationDispatch.id.in_(ids)
                        )
                    )
            if session_ids:
                # 本会话触发的系统编排运行与节点记录一并清理（壳工作流保留：单例模板）
                runs = (
                    (
                        await db.scalars(
                            select(WorkflowRun).where(
                                WorkflowRun.triggered_by.in_(
                                    [f"session:{sid}" for sid in session_ids]
                                )
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
                # 自动汇总消息一并清理（SQLite 未必启用 FK 级联）
                await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
                await db.execute(sa_delete(ChatSession).where(ChatSession.id.in_(session_ids)))
            await db.commit()
    finally:
        await engine.dispose()


async def _wait_dispatch_terminal(dispatch_uid: str, timeout_seconds: float = 10.0) -> str:
    """轮询派发状态到终态（编排后台执行是异步的，等它收口）。"""

    engine = create_db_engine(get_settings().database_url)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        status = ""
        while loop.time() < deadline:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                row = (
                    await db.scalars(
                        select(InvestigationDispatch).where(
                            InvestigationDispatch.dispatch_uid == dispatch_uid
                        )
                    )
                ).first()
                status = row.status if row is not None else ""
            if status in ("succeeded", "failed", "stopped"):
                # 等编排图自身收尾（join/节点落库）再返回，避免测试清理与后台任务赛跑
                await asyncio.sleep(0.1)
                return status
            await asyncio.sleep(0.05)
        raise AssertionError(f"派发未在 {timeout_seconds}s 内收口: status={status}")
    finally:
        await engine.dispose()


def _make_result(
    worker_uid: str, status: str, findings: list[dict] | None = None
) -> WorkerResult:
    return WorkerResult(
        worker_uid=worker_uid,
        status=status,
        conclusion="结论",
        findings=findings or [],
        usage={"total_tokens": 100},
        duration_ms=50,
    )


async def _insert_session(tag: str) -> list[str]:
    engine = create_db_engine(get_settings().database_url)
    session_id = f"{tag}-s"
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(
                ChatSession(
                    id=session_id,
                    title=f"inv-{tag}",
                    provider="deepseek",
                    model="deepseek-flash",
                    adk_user_id="user-1",
                )
            )
            await db.commit()
    finally:
        await engine.dispose()
    return [session_id]


def test_parse_findings_validates_call_ids() -> None:
    """findings 围栏解析：编造的 call_id 证据必须被丢弃；解析失败兜底纯文本。"""

    text = (
        "中间推理\n```findings\n"
        + json.dumps(
            {
                "conclusion": "结论A",
                "findings": [
                    {
                        "title": "发现1",
                        "detail": "细节1",
                        "evidence": [
                            {"call_id": "real-1", "tool": "query", "excerpt": "x=1"},
                            {"call_id": "made-up", "tool": "query", "excerpt": "y=2"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    conclusion, findings = parse_findings(text, {"real-1"})
    assert conclusion == "结论A"
    assert len(findings) == 1
    assert [e["call_id"] for e in findings[0]["evidence"]] == ["real-1"]

    conclusion2, findings2 = parse_findings("纯文本结论", set())
    assert conclusion2 == "纯文本结论"
    assert findings2 == []


@pytest.mark.asyncio
async def test_worker_gate_blocks_write_names() -> None:
    """worker 闸门：禁写启发式命中返回错误（不进审批），只读查询放行。"""

    gate = build_worker_tool_gate()

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    assert await gate(_Tool("mcp__x__create_dashboard"), {}, None) is not None
    assert await gate(_Tool("run_workflow"), {}, None) is not None
    assert await gate(_Tool("mcp__x__get_run_history"), {}, None) is None
    assert await gate(_Tool("query_range"), {}, None) is None
    # 联网搜索是只读查询，闸门放行（挂载与否由会话开关决定）
    assert await gate(_Tool("web_search"), {}, None) is None


def test_dispatch_budget_defaults() -> None:
    """派发/计划工具默认预算与 WorkerBudget 缺省一致，多处不许漂移。"""

    import inspect

    from app.tools.investigation import dispatch_investigation
    from app.tools.plan import create_plan

    defaults = WorkerBudget()
    params = inspect.signature(dispatch_investigation).parameters
    assert params["max_steps_per_worker"].default == defaults.max_steps
    assert params["timeout_seconds_per_worker"].default == defaults.timeout_seconds
    assert params["token_budget_per_worker"].default == defaults.token_budget
    plan_params = inspect.signature(create_plan).parameters
    assert plan_params["max_steps_per_step"].default == defaults.max_steps
    assert plan_params["timeout_seconds_per_step"].default == defaults.timeout_seconds
    assert plan_params["token_budget_per_step"].default == defaults.token_budget


@pytest.mark.asyncio
async def test_dispatch_runs_to_completion(monkeypatch) -> None:
    """编排等全部路收口：发现编号按完成顺序分配；运行记录落在工作流运行里。"""

    tag = f"iv-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    dispatch_uids: list[str] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_worker(*, worker_uid, name, publish, stop_check, **kwargs):
        await asyncio.sleep(0.05 if name == "快路" else 0.15)
        return _make_result(
            worker_uid, "succeeded", [{"title": f"{name}发现", "detail": "d", "evidence": []}]
        )

    monkeypatch.setattr(dispatch_service, "_assemble_worker", fake_assemble)
    monkeypatch.setattr(dispatch_service, "run_worker", fake_run_worker)

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            dispatch = await dispatch_service.start_dispatch(
                db,
                session_id=session_ids[0],
                question="为什么告警异常",
                workers=[
                    {"name": "快路", "target": "指标", "instruction": ""},
                    {"name": "慢路", "target": "日志", "instruction": ""},
                ],
                budget=WorkerBudget(max_steps=4, timeout_seconds=30, token_budget=5000),
                created_by=None,
            )
            dispatch_uids.append(dispatch.dispatch_uid)

        status = await _wait_dispatch_terminal(dispatch.dispatch_uid)
        assert status == "succeeded"

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            wire = await dispatch_service.get_dispatch_wire(db, dispatch.dispatch_uid)
        # 两路都收口：发现全局编号 1、2（完成顺序分配）
        assert {f["n"] for f in wire["findings"]} == {1, 2}
        assert wire["stats"]["succeeded"] == 2

        # 运行记录打通：这次编排是一条系统工作流运行，节点记录含任务与汇合
        run_row = (
            (
                await db.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.trigger == "investigation",
                        WorkflowRun.triggered_by == f"session:{session_ids[0]}",
                    )
                    .order_by(WorkflowRun.id.desc())
                )
            )
            .first()
        )
        assert run_row is not None
        assert run_row.status == "succeeded"
        node_runs = (
            (
                await db.scalars(
                    select(WorkflowNodeRun).where(WorkflowNodeRun.run_id == run_row.id)
                )
            )
            .all()
        )
        assert {"task", "join"} <= {r.node_kind for r in node_runs}
    finally:
        await _cleanup(session_ids, dispatch_uids)


@pytest.mark.asyncio
async def test_retry_renumbers_findings(monkeypatch) -> None:
    """重试失败路：旧发现转 retired，新发现追加新号（编号不复用）。"""

    tag = f"iv-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    dispatch_uids: list[str] = []
    state = {"log_attempts": 0}

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_worker(*, worker_uid, name, **kwargs):
        if name == "日志员":
            state["log_attempts"] += 1
            if state["log_attempts"] == 1:
                return _make_result(worker_uid, "failed")
            return _make_result(
                worker_uid, "succeeded", [{"title": "重试发现", "detail": "d", "evidence": []}]
            )
        return _make_result(
            worker_uid, "succeeded", [{"title": "指标发现", "detail": "d", "evidence": []}]
        )

    monkeypatch.setattr(dispatch_service, "_assemble_worker", fake_assemble)
    monkeypatch.setattr(dispatch_service, "run_worker", fake_run_worker)

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            dispatch = await dispatch_service.start_dispatch(
                db,
                session_id=session_ids[0],
                question="排查",
                workers=[
                    {"name": "指标员", "target": "指标", "instruction": ""},
                    {"name": "日志员", "target": "日志", "instruction": ""},
                ],
                budget=WorkerBudget(),
                created_by=None,
            )
            dispatch_uids.append(dispatch.dispatch_uid)
        assert await _wait_dispatch_terminal(dispatch.dispatch_uid) == "succeeded"

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            wire = await dispatch_service.get_dispatch_wire(db, dispatch.dispatch_uid)
        assert wire["stats"]["failed"] == 1
        assert [f["n"] for f in wire["findings"] if not f["retired"]] == [1]

        failed = next(w for w in wire["workers"] if w["name"] == "日志员")
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            row = await dispatch_service.retry_worker(
                db, dispatch.dispatch_uid, failed["worker_uid"]
            )
            assert row is not None and row.attempt == 2
        assert await _wait_dispatch_terminal(dispatch.dispatch_uid) == "succeeded"

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            final = await dispatch_service.get_dispatch_wire(db, dispatch.dispatch_uid)
        active = [f for f in final["findings"] if not f["retired"]]
        # 1 号（指标）保留；重试路新发现拿新号 2，不复用
        assert {f["n"] for f in active} == {1, 2}
    finally:
        await _cleanup(session_ids, dispatch_uids)


@pytest.mark.asyncio
async def test_stop_dispatch_marks_workers_stopped(monkeypatch) -> None:
    """全部停止：协作停止标记让跑着的 worker 退出，派发状态 stopped。"""

    tag = f"iv-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    dispatch_uids: list[str] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_worker(*, worker_uid, stop_check, **kwargs):
        # 模拟长跑：只在事件间隙响应停止
        for _ in range(600):
            if stop_check():
                return _make_result(worker_uid, "stopped")
            await asyncio.sleep(0.01)
        return _make_result(worker_uid, "failed")

    monkeypatch.setattr(dispatch_service, "_assemble_worker", fake_assemble)
    monkeypatch.setattr(dispatch_service, "run_worker", fake_run_worker)

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            dispatch = await dispatch_service.start_dispatch(
                db,
                session_id=session_ids[0],
                question="排查",
                workers=[{"name": "长跑路", "target": "目标", "instruction": ""}],
                budget=WorkerBudget(),
                created_by=None,
            )
            dispatch_uids.append(dispatch.dispatch_uid)

        await asyncio.sleep(0.05)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            assert await dispatch_service.stop_dispatch(db, dispatch.dispatch_uid) is True
        assert await _wait_dispatch_terminal(dispatch.dispatch_uid) == "stopped"

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            wire = await dispatch_service.get_dispatch_wire(db, dispatch.dispatch_uid)
        assert wire["status"] == "stopped"
        assert wire["workers"][0]["status"] == "stopped"
    finally:
        await _cleanup(session_ids, dispatch_uids)


def test_parse_workers_json_accepts_tool_focus() -> None:
    """worker 规格可选 tools 声明：规整后随 spec 传到装配收窄挂载面。"""

    specs = dispatch_service.parse_workers_json(
        json.dumps([{"name": "甲", "target": "目标", "tools": ["query_knowledge", "", "web_search"]}])
    )
    assert specs[0]["tools"] == ["query_knowledge", "web_search"]
    specs2 = dispatch_service.parse_workers_json(
        json.dumps([{"name": "乙", "target": "目标"}])
    )
    assert specs2[0]["tools"] == []


def test_filter_tools_by_focus_narrows_mounts() -> None:
    """按声明收窄挂载面：命中保留（知识闭包版按 query_knowledge 匹配），未声明全量。"""

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [
        _Tool("recall_memory"),
        _Tool("scoped_query_knowledge"),
        _Tool("web_search"),
        _Tool("mcp__x__query"),
    ]
    kept = filter_tools_by_focus(tools, {"query_knowledge", "mcp__x__query"})
    assert [t.name for t in kept] == ["scoped_query_knowledge", "mcp__x__query"]
    assert filter_tools_by_focus(tools, None) is tools
    assert filter_tools_by_focus(tools, ()) is tools


def test_worker_agent_injects_focus_guidance_and_focus_mounts() -> None:
    """统筹纪律在唯一装配入口注入；tool_focus 非空时收窄挂载面。"""

    kwargs = dict(
        provider_id="custom",
        model="m",
        model_prefix="openai/",
        base_url="https://example.com/v1",
        api_key="k",
        name="w",
        target="t",
        instruction="",
    )
    agent = build_worker_agent(**kwargs)
    assert "聚焦纪律" in (agent.instruction or "")
    assert "无关的工具一律不调用" in (agent.instruction or "")
    # 默认全量只读面：工作流查询等都挂着
    default_names = {getattr(t, "name", "") for t in agent.tools}
    assert {"recall_memory", "list_workflows", "query_knowledge"} <= default_names

    focused = build_worker_agent(**kwargs, tool_focus=("query_knowledge",))
    focused_names = {getattr(t, "name", "") for t in focused.tools}
    assert "query_knowledge" in focused_names
    assert "recall_memory" not in focused_names
    assert "list_workflows" not in focused_names
