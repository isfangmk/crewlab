"""并行排查编排：并行执行、软截止返回、后台续跑、发现编号与停止。

执行缝（worker 的装配与 LLM 执行）打桩，落库/事件总线/编号/状态机全走真实实现；
用例自带专属 tag，finally 全清理（沿用 test_share_admin 口径）。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import create_db_engine
from app.models.chat import ChatSession
from app.models.investigation import InvestigationDispatch, InvestigationWorkerRun
from app.services.investigation import dispatch_service
from app.services.investigation.worker_runner import (
    WorkerBudget,
    WorkerResult,
    build_worker_tool_gate,
    parse_findings,
)


async def _cleanup(session_ids: list[str], dispatch_uids: list[str]) -> None:
    """插桩数据全清理：worker 行、派发行、临时会话。"""

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
                await db.execute(sa_delete(ChatSession).where(ChatSession.id.in_(session_ids)))
            await db.commit()
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
    """派发工具默认预算与 WorkerBudget 缺省一致，两处不许漂移。"""

    import inspect

    from app.tools.investigation import dispatch_investigation

    params = inspect.signature(dispatch_investigation).parameters
    assert params["max_steps_per_worker"].default == 12
    assert params["timeout_seconds_per_worker"].default == 300
    assert params["token_budget_per_worker"].default == 60000
    assert WorkerBudget() == WorkerBudget(max_steps=12, timeout_seconds=300, token_budget=60000)


@pytest.mark.asyncio
async def test_worker_web_search_requires_session_toggle() -> None:
    """联网搜索只在对话开启 web_search 时挂载；未开启直接 None。"""

    from app.db import SessionLocal
    from app.services.investigation.dispatch_service import _worker_web_search_tool

    session = ChatSession(id=f"websearch-{uuid.uuid4().hex[:8]}", title="t", web_search_enabled=False)
    async with SessionLocal() as db:
        assert await _worker_web_search_tool(db, session) is None


@pytest.mark.asyncio
async def test_dispatch_soft_deadline_and_background_completion(monkeypatch) -> None:
    """软截止先回初步结果，未完的路后台续跑；全局发现编号按完成顺序分配。"""

    tag = f"iv-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    dispatch_uids: list[str] = []
    gate_slow = asyncio.Event()

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_worker(*, worker_uid, name, publish, stop_check, **kwargs):
        if name == "慢路":
            # 慢路：外部放行后才完成，模拟 LLM 长跑
            await gate_slow.wait()
            return _make_result(
                worker_uid, "succeeded", [{"title": "慢发现", "detail": "d", "evidence": []}]
            )
        await asyncio.sleep(0.05)
        return _make_result(
            worker_uid, "succeeded", [{"title": "快发现", "detail": "d", "evidence": []}]
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

        # 软截止 1s：慢路未完 → False（初步结果），发现只含快路的 1 号
        complete = await dispatch_service.wait_dispatch(dispatch.dispatch_uid, 1)
        assert complete is False
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            partial = await dispatch_service.tool_result_wire(db, dispatch.dispatch_uid)
        assert partial["partial"] is True
        assert [w["name"] for w in partial["still_running"]] == ["慢路"]
        assert [f["n"] for f in partial["findings"]] == [1]

        # 放行慢路 → 后台续跑完成 → 全部终态
        gate_slow.set()
        complete2 = await dispatch_service.wait_dispatch(dispatch.dispatch_uid, 10)
        assert complete2 is True
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            final = await dispatch_service.tool_result_wire(db, dispatch.dispatch_uid)
        assert final["partial"] is False
        assert {f["n"] for f in final["findings"]} == {1, 2}
        assert final["status"] == "succeeded"
    finally:
        gate_slow.set()
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
        assert await dispatch_service.wait_dispatch(dispatch.dispatch_uid, 10) is True

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
        assert await dispatch_service.wait_dispatch(dispatch.dispatch_uid, 10) is True

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
        assert await dispatch_service.wait_dispatch(dispatch.dispatch_uid, 10) is True

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            wire = await dispatch_service.get_dispatch_wire(db, dispatch.dispatch_uid)
        assert wire["status"] == "stopped"
        assert wire["workers"][0]["status"] == "stopped"
    finally:
        await _cleanup(session_ids, dispatch_uids)
