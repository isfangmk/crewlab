"""规划执行编排：顺序执行、失败暂停与重试/跳过/停止、暂停续跑与事件通道复位。

执行缝（步骤装配与 LLM 执行）打桩，落库/事件总线/状态机全走真实实现；
用例自带专属 tag，finally 全清理（沿用 test_investigation_service 口径）。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adk_runtime.headless import HeadlessResult, WorkerBudget
from app.config import get_settings
from app.db import create_db_engine
from app.models.chat import ChatSession
from app.models.plan import PlanExecution, PlanStep
from app.services.investigation.investigation_events import InvestigationEventBus
from app.services.plan import plan_service


async def _cleanup(session_ids: list[str], plan_uids: list[str]) -> None:
    """插桩数据全清理：步骤行、计划行、临时会话。"""

    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if plan_uids:
                rows = (
                    (
                        await db.scalars(
                            select(PlanExecution).where(
                                PlanExecution.plan_uid.in_(plan_uids)
                            )
                        )
                    )
                    .all()
                )
                ids = [r.id for r in rows]
                if ids:
                    await db.execute(sa_delete(PlanStep).where(PlanStep.plan_id.in_(ids)))
                    await db.execute(
                        sa_delete(PlanExecution).where(PlanExecution.id.in_(ids))
                    )
            if session_ids:
                await db.execute(sa_delete(ChatSession).where(ChatSession.id.in_(session_ids)))
            await db.commit()
    finally:
        await engine.dispose()


async def _insert_session(tag: str) -> list[str]:
    engine = create_db_engine(get_settings().database_url)
    session_id = f"{tag}-s"
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(
                ChatSession(
                    id=session_id,
                    title=f"plan-{tag}",
                    provider="deepseek",
                    model="deepseek-flash",
                    adk_user_id="user-1",
                )
            )
            await db.commit()
    finally:
        await engine.dispose()
    return [session_id]


async def _create_plan(tag: str, session_id: str, steps: list[dict]) -> PlanExecution:
    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await plan_service.create_plan(
                db,
                session_id=session_id,
                title=f"计划-{tag}",
                question="总目标",
                steps=steps,
                budget=WorkerBudget(max_steps=4, timeout_seconds=30, token_budget=5000),
                created_by=None,
            )
    finally:
        await engine.dispose()


async def _wait_plan_status(plan_uid: str, statuses: set[str], timeout: float = 10.0) -> str:
    """轮询计划状态直到进入目标集合；超时直接断言失败。"""

    engine = create_db_engine(get_settings().database_url)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                row = (
                    await db.scalars(
                        select(PlanExecution).where(PlanExecution.plan_uid == plan_uid)
                    )
                ).first()
                if row is not None and row.status in statuses:
                    return row.status
            if loop.time() > deadline:
                raise AssertionError(f"计划 {plan_uid} 未在 {timeout}s 内进入 {statuses}")
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def _wait_step_running(plan_id: int, timeout: float = 10.0) -> None:
    """轮询直到有步骤进入 running；每次开新会话，避免身份映射返回旧值。"""

    engine = create_db_engine(get_settings().database_url)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                rows = (
                    (await db.scalars(select(PlanStep).where(PlanStep.plan_id == plan_id)))
                    .all()
                )
                if any(r.status == "running" for r in rows):
                    return
            if loop.time() > deadline:
                raise AssertionError("步骤未在超时内进入 running")
            await asyncio.sleep(0.02)
    finally:
        await engine.dispose()


async def _wire(plan_uid: str) -> dict:
    engine = create_db_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            wire = await plan_service.get_plan_wire(db, plan_uid)
    finally:
        await engine.dispose()
    assert wire is not None
    return wire


def _ok(name: str) -> HeadlessResult:
    return HeadlessResult(
        status="succeeded",
        conclusion=f"结论{name}",
        usage={"total_tokens": 100},
        duration_ms=10,
        log_text="调用工具 query_knowledge\n",
    )


def test_parse_steps_json_validates() -> None:
    """步骤 JSON 校验：数量边界、空名、工具类型都要拦住；合法输入规整。"""

    with pytest.raises(ValueError):
        plan_service.parse_steps_json("not-json")
    with pytest.raises(ValueError):
        plan_service.parse_steps_json("{}")
    with pytest.raises(ValueError):
        plan_service.parse_steps_json("[]")
    with pytest.raises(ValueError):
        plan_service.parse_steps_json(json.dumps([{"name": f"s{i}"} for i in range(13)]))
    with pytest.raises(ValueError):
        plan_service.parse_steps_json(json.dumps([{"name": "  "}]))
    with pytest.raises(ValueError):
        plan_service.parse_steps_json(json.dumps([{"name": "a", "tools": "web_search"}]))

    specs = plan_service.parse_steps_json(
        json.dumps(
            [
                {
                    "name": " 步骤一 ",
                    "instruction": " 做点什么 ",
                    "tools": ["web_search", "", "run_python"],
                }
            ]
        )
    )
    assert specs == [
        {
            "name": "步骤一",
            "instruction": "做点什么",
            "tools": ["web_search", "run_python"],
        }
    ]


@pytest.mark.asyncio
async def test_create_plan_draft_and_update_steps() -> None:
    """create_plan 落 draft 与步骤行；待确认可整体替换步骤。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []
    try:
        plan = await _create_plan(
            tag,
            session_ids[0],
            [
                {"name": "第一步", "instruction": "a", "tools": ["web_search"]},
                {"name": "第二步", "instruction": "b"},
            ],
        )
        plan_uids.append(plan.plan_uid)
        wire = await _wire(plan.plan_uid)
        assert wire["status"] == "draft"
        assert wire["stats"]["total"] == 2
        assert [s["name"] for s in wire["steps"]] == ["第一步", "第二步"]
        assert all(s["status"] == "pending" for s in wire["steps"])

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                ok = await plan_service.update_steps(
                    db, plan.plan_uid, [{"name": "新步骤", "instruction": "x", "tools": []}]
                )
        finally:
            await engine.dispose()
        assert ok is True
        wire2 = await _wire(plan.plan_uid)
        assert [s["name"] for s in wire2["steps"]] == ["新步骤"]
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_plan_runs_steps_in_order_with_prior_context(monkeypatch) -> None:
    """顺序执行：每步结论进下一步上下文；已执行的计划不能再改步骤。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []
    prompts: list[tuple[str, str]] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_step(*, name, prompt, **_kwargs):
        prompts.append((name, prompt))
        return _ok(name)

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(
            tag,
            session_ids[0],
            [{"name": "步骤一"}, {"name": "步骤二"}, {"name": "步骤三"}],
        )
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"succeeded", "paused", "stopped"}) == "succeeded"

        # 执行顺序与前置结论传递
        assert [name for name, _ in prompts] == ["步骤一", "步骤二", "步骤三"]
        assert "结论步骤一" not in prompts[0][1]
        assert "结论步骤一" in prompts[1][1]
        assert "结论步骤一" in prompts[2][1] and "结论步骤二" in prompts[2][1]

        wire = await _wire(plan.plan_uid)
        assert [s["status"] for s in wire["steps"]] == ["succeeded"] * 3
        assert [s["summary"] for s in wire["steps"]] == ["结论步骤一", "结论步骤二", "结论步骤三"]
        assert wire["stats"]["tool_calls"] == 3
        assert wire["used_tokens"] == 300

        # 已执行的计划不允许再调整步骤
        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                ok = await plan_service.update_steps(db, plan.plan_uid, [{"name": "x"}])
        finally:
            await engine.dispose()
        assert ok is False
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_step_failure_pauses_and_retry_resumes(monkeypatch) -> None:
    """单步失败即暂停等决定；重试此步 attempt+1 原地重跑并继续。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []
    attempts: dict[str, int] = {}

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_step(*, name, **_kwargs):
        attempts[name] = attempts.get(name, 0) + 1
        if name == "步骤二" and attempts[name] == 1:
            return HeadlessResult(status="failed", error="boom", duration_ms=5, log_text="")
        return _ok(name)

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(
            tag, session_ids[0], [{"name": "步骤一"}, {"name": "步骤二"}, {"name": "步骤三"}]
        )
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"paused", "succeeded", "stopped"}) == "paused"
        wire = await _wire(plan.plan_uid)
        step2 = next(s for s in wire["steps"] if s["name"] == "步骤二")
        assert step2["status"] == "failed"
        assert step2["error"] == "boom"
        # 失败只影响本步：其后步骤还在排队
        step3 = next(s for s in wire["steps"] if s["name"] == "步骤三")
        assert step3["status"] == "pending"

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.retry_step(db, plan.plan_uid, step2["step_uid"]) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"succeeded", "paused", "stopped"}) == "succeeded"
        wire2 = await _wire(plan.plan_uid)
        step2b = next(s for s in wire2["steps"] if s["name"] == "步骤二")
        assert step2b["status"] == "succeeded"
        assert step2b["attempt"] == 2
        assert [s["status"] for s in wire2["steps"]] == ["succeeded"] * 3
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_skip_step_continues_with_placeholder(monkeypatch) -> None:
    """跳过失败步骤：占位结论并继续，汇总时提示缺口。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_step(*, name, **_kwargs):
        if name == "步骤二":
            return HeadlessResult(status="failed", error="boom", duration_ms=5, log_text="")
        return _ok(name)

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(
            tag, session_ids[0], [{"name": "步骤一"}, {"name": "步骤二"}, {"name": "步骤三"}]
        )
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"paused", "succeeded", "stopped"}) == "paused"
        wire = await _wire(plan.plan_uid)
        step2 = next(s for s in wire["steps"] if s["name"] == "步骤二")

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.skip_step(db, plan.plan_uid, step2["step_uid"]) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"succeeded", "paused", "stopped"}) == "succeeded"
        wire2 = await _wire(plan.plan_uid)
        step2b = next(s for s in wire2["steps"] if s["name"] == "步骤二")
        assert step2b["status"] == "skipped"
        assert "缺口" in step2b["summary"]
        assert wire2["steps"][2]["status"] == "succeeded"
        assert wire2["stats"]["skipped"] == 1
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_stop_plan_marks_pending_stopped(monkeypatch) -> None:
    """全部停止：执行中的步骤断协作，未执行步骤全部转 stopped。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_step(*, stop_check, **_kwargs):
        # 长跑步骤：只响应停止/暂停信号
        while not stop_check():
            await asyncio.sleep(0.02)
        return HeadlessResult(status="stopped", duration_ms=5, log_text="")

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(tag, session_ids[0], [{"name": "步骤一"}, {"name": "步骤二"}])
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()
        # 等第一步真正跑起来再停
        await _wait_step_running(plan.id)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.stop_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"stopped"}) == "stopped"
        wire = await _wire(plan.plan_uid)
        assert [s["status"] for s in wire["steps"]] == ["stopped", "stopped"]
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_pause_then_resume_reexecutes_step(monkeypatch) -> None:
    """暂停把执行中的步骔回退待执行；继续执行重跑该步直到完成。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []
    invocations: list[str] = []

    async def fake_assemble(**_kwargs):
        return object()

    async def fake_run_step(*, name, stop_check, **_kwargs):
        invocations.append(name)
        if name == "步骤一" and invocations.count("步骤一") == 1:
            while not stop_check():
                await asyncio.sleep(0.02)
            return HeadlessResult(status="stopped", duration_ms=5, log_text="")
        return _ok(name)

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(tag, session_ids[0], [{"name": "步骤一"}, {"name": "步骤二"}])
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()
        await _wait_step_running(plan.id)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.pause_plan(plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"paused"}) == "paused"
        wire = await _wire(plan.plan_uid)
        # 协作停止的步骤退回待执行，续跑时原地重跑
        assert wire["steps"][0]["status"] == "pending"

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"succeeded", "paused", "stopped"}) == "succeeded"
        assert invocations == ["步骤一", "步骤一", "步骤二"]
    finally:
        await _cleanup(session_ids, plan_uids)


@pytest.mark.asyncio
async def test_event_channel_reopens_after_terminal() -> None:
    """终态后同一 uid 重开运行：生命周期复位但缓冲保留，订阅不再被判终态。"""

    bus = InvestigationEventBus()
    uid = f"evt-{uuid.uuid4().hex[:8]}"

    publish = bus.open(uid, terminal_events=("plan_finished",))
    await publish({"event": "plan_finished", "plan_uid": uid, "status": "stopped"})
    assert bus.is_finished(uid) is True

    publish2 = bus.open(uid, terminal_events=("plan_finished",))
    assert bus.is_finished(uid) is False
    backlog, _queue = await bus.subscribe(uid)
    # 旧缓冲保留可补发：重开不清历史
    assert any(e.get("event") == "plan_finished" for e in backlog)

    await publish2({"event": "plan_finished", "plan_uid": uid, "status": "succeeded"})
    assert bus.is_finished(uid) is True


@pytest.mark.asyncio
async def test_step_tools_focus_passed_to_assemble(monkeypatch) -> None:
    """步骤声明的 tools 传到装配收窄挂载面；未声明传空（挂默认全量）。"""

    tag = f"pl-{uuid.uuid4().hex[:8]}"
    session_ids = await _insert_session(tag)
    plan_uids: list[str] = []
    captured: list[tuple[str, ...]] = []

    async def fake_assemble(**kwargs):
        captured.append(tuple(kwargs.get("tools") or ()))
        return object()

    async def fake_run_step(*, name, **_kwargs):
        return _ok(name)

    monkeypatch.setattr(plan_service, "_assemble_step", fake_assemble)
    monkeypatch.setattr(plan_service, "run_plan_step", fake_run_step)

    try:
        plan = await _create_plan(
            tag,
            session_ids[0],
            [
                {"name": "有声明", "instruction": "", "tools": ["web_search"]},
                {"name": "无声明", "instruction": ""},
            ],
        )
        plan_uids.append(plan.plan_uid)

        engine = create_db_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                assert await plan_service.start_plan(db, plan.plan_uid) is True
        finally:
            await engine.dispose()

        assert await _wait_plan_status(plan.plan_uid, {"succeeded", "paused", "stopped"}) == "succeeded"
        assert captured == [("web_search",), ()]
    finally:
        await _cleanup(session_ids, plan_uids)
