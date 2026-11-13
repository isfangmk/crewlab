"""沙盒执行记录：服务层的行为。

覆盖范围：
  - 工作流代码节点的沙盒脚本协议：结果信封解析、读取键登记、失败信封；
  - 执行记录查询：来源 / 状态筛选、关键字、服务端分页、概览汇总；
  - 执行记录落库：成功与失败都要留下一行。
不覆盖：真实容器里的执行（依赖 Docker 环境；由 test_sandbox_execution.py 覆盖）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete

from app.adk_runtime.workflow_node_impl import (
    SANDBOX_RESULT_MARKER,
    _build_sandbox_script,
    _parse_sandbox_payload,
    _parse_sandbox_reads,
)
from app.models_orm.sandbox import SandboxExecution
from app.services import sandbox_query_service, sandbox_service


@asynccontextmanager
async def _db_sessions() -> AsyncGenerator[object, None]:
    """用例独立的 sessionmaker。

    模块级 engine 会跨事件循环复用连接池，而每个 async 用例跑在自己的循环里，
    直接用它会在下一次用例里报 Event loop is closed。
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# ---------------- 沙盒脚本协议 ----------------


def test_sandbox_script_wraps_state_and_code() -> None:
    """脚本要把状态和节点代码都带进去，否则容器里跑不起来。"""

    script = _build_sandbox_script(
        code="def run(state):\n    return state['a']\n",
        state={"a": 7},
    )
    # 状态以 JSON 形式内联，不能是 Python repr（容器里没有宿主的对象）
    assert json.dumps({"a": 7}, ensure_ascii=False) in script
    assert "def run(state)" in script
    assert SANDBOX_RESULT_MARKER in script


def test_sandbox_script_handles_non_serializable_state() -> None:
    """状态里出现不可 JSON 化的值时不能崩，要能降级成字符串。"""

    class Weird:
        pass

    script = _build_sandbox_script(
        code="def run(state):\n    return 1\n",
        state={"x": Weird()},
    )
    assert "def run(state)" in script


def test_parse_payload_reads_result_and_reads() -> None:
    """结果信封里既有返回值也有读到的键，两者都要取出来。"""

    stdout = (
        "用户代码的普通输出\n"
        f"{SANDBOX_RESULT_MARKER}"
        + json.dumps(
            {"result": {"total": 5}, "reads": {"a": 2, "b": 3}, "error": ""},
            ensure_ascii=False,
        )
        + "\n"
    )
    payload = _parse_sandbox_payload(stdout)
    assert payload["result"] == {"total": 5}
    assert _parse_sandbox_reads(stdout) == {"a": 2, "b": 3}


def test_parse_payload_takes_the_last_envelope() -> None:
    """代码自己也可能打印标记行，要取最后那个真正的信封。"""

    stdout = (
        f"{SANDBOX_RESULT_MARKER}{{}}\n"
        f"{SANDBOX_RESULT_MARKER}"
        + json.dumps({"result": 1, "reads": {}, "error": ""})
        + "\n"
    )
    assert _parse_sandbox_payload(stdout)["result"] == 1


def test_parse_payload_raises_when_envelope_missing() -> None:
    """没有信封说明脚本没跑到发结果那步，不能当成空结果。"""

    with pytest.raises(RuntimeError, match="沙盒没有返回结果信封"):
        _parse_sandbox_payload("只有普通输出")


def test_parse_reads_returns_empty_when_envelope_missing() -> None:
    """取不到信封时读到的键按空处理，失败路径不该因此再抛一次。"""

    assert _parse_sandbox_reads("普通输出") == {}


# ---------------- 执行记录查询 ----------------


async def _make_record(db, **kwargs) -> SandboxExecution:
    """插入一条执行记录。"""

    defaults = {
        "id": "exec-test-1",
        "source": "workflow",
        "source_ref": "node1",
        "session_id": "sandbox-test-session",
        "workflow_run_id": "run-1",
        "language": "python",
        "image": "python:3.12-slim",
        "network_enabled": False,
        "code": "print(1)",
        "stdout": "1\n",
        "stderr": "",
        "exit_code": 0,
        "status": "succeeded",
        "error": None,
        "duration_ms": 100,
    }
    defaults.update(kwargs)
    row = SandboxExecution(**defaults)
    db.add(row)
    await db.commit()
    return row


async def _clear(db, prefix: str) -> None:
    await db.execute(delete(SandboxExecution).where(SandboxExecution.id.like(prefix)))
    await db.commit()


@pytest.mark.asyncio
async def test_list_filters_by_source_and_status() -> None:
    """来源与状态筛选要真的过滤，而不是把筛选条件忽略掉。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            await _clear(db, "exec-test-%")
            await _make_record(db, id="exec-test-a", source="workflow", status="succeeded")
            await _make_record(db, id="exec-test-b", source="skill", status="failed")
            try:
                rows, total = await sandbox_query_service.list_executions(
                    db, source="skill", session_id="sandbox-test-session",
                    limit=50, offset=0,
                )
                assert total == 1
                assert rows[0].source == "skill"

                rows, total = await sandbox_query_service.list_executions(
                    db, status="failed", session_id="sandbox-test-session",
                    limit=50, offset=0,
                )
                assert total == 1
                assert rows[0].status == "failed"
            finally:
                await _clear(db, "exec-test-%")


@pytest.mark.asyncio
async def test_list_rejects_unknown_source_and_status() -> None:
    """非法筛选值要报错，不能静默返回空列表让人以为「没有记录」。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            with pytest.raises(ValueError, match="不支持的 source"):
                await sandbox_query_service.list_executions(db, source="未知来源")
            with pytest.raises(ValueError, match="不支持的 status"):
                await sandbox_query_service.list_executions(db, status="未知状态")


@pytest.mark.asyncio
async def test_list_paginates_with_offset() -> None:
    """分页要真的按 offset 切片，total 是筛选后的总数。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            await _clear(db, "exec-page-%")
            for i in range(3):
                await _make_record(db, id=f"exec-page-{i}", source="manual")
            try:
                rows, total = await sandbox_query_service.list_executions(
                    db, source="manual", session_id="sandbox-test-session",
                    limit=2, offset=0,
                )
                assert total == 3
                assert len(rows) == 2

                rows, total = await sandbox_query_service.list_executions(
                    db, source="manual", session_id="sandbox-test-session",
                    limit=2, offset=2,
                )
                assert total == 3
                assert len(rows) == 1
            finally:
                await _clear(db, "exec-page-%")


@pytest.mark.asyncio
async def test_list_matches_keyword_across_fields() -> None:
    """关键字要能命中来源标识、代码正文与输出。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            await _clear(db, "exec-kw-%")
            await _make_record(db, id="exec-kw-1", source_ref="节点abc", stdout="结果xyz\n")
            try:
                rows, total = await sandbox_query_service.list_executions(
                    db, q="节点abc", session_id="sandbox-test-session",
                    limit=50, offset=0,
                )
                assert total == 1
                assert rows[0].source_ref == "节点abc"

                rows, total = await sandbox_query_service.list_executions(
                    db, q="结果xyz", session_id="sandbox-test-session",
                    limit=50, offset=0,
                )
                assert total == 1
            finally:
                await _clear(db, "exec-kw-%")


@pytest.mark.asyncio
async def test_summary_counts_by_source_and_status() -> None:
    """概览要能按来源与状态给出数量。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            summary = await sandbox_query_service.summarize(db)
            assert summary["total"] >= 0
            assert isinstance(summary["by_source"], dict)
            assert isinstance(summary["by_status"], dict)
            assert summary["avg_duration_ms"] >= 0


# ---------------- 落库 ----------------


@pytest.mark.asyncio
async def test_record_is_written_with_source_context() -> None:
    """每次执行都要留一行，带上来源、代码、输出与耗时。"""

    async with _db_sessions() as maker:
        async with maker() as db:
            await _clear(db, "exec-write-%")
            await sandbox_service._record(
                db,
                execution_id="exec-write-1",
                ctx=sandbox_service.SandboxContext(
                    source=sandbox_service.SOURCE_SKILL,
                    source_ref="chart-builder",
                    session_id="sess-9",
                ),
                image="python:3.12-slim",
                network_enabled=False,
                code="print('hi')",
                stdout="hi\n",
                stderr="",
                exit_code=0,
                status="succeeded",
                error="",
                duration_ms=42,
            )
            try:
                row = await db.get(SandboxExecution, "exec-write-1")
                assert row is not None
                assert row.source == "skill"
                assert row.source_ref == "chart-builder"
                assert row.session_id == "sess-9"
                assert row.stdout == "hi\n"
                assert row.exit_code == 0
                assert row.status == "succeeded"
                assert row.duration_ms == 42
                assert row.image == "python:3.12-slim"
            finally:
                await _clear(db, "exec-write-%")


def test_truncate_marks_cut_text() -> None:
    """超长正文要截断并标注原长度，让人知道这不是全部。"""

    text = "x" * 100
    out = sandbox_service._truncate(text, 10)
    assert out.startswith("x" * 10)
    assert "已截断" in out
    assert "100" in out
    # 没超长时原样返回
    assert sandbox_service._truncate("short", 10) == "short"


def test_execution_to_wire_exposes_contract() -> None:
    """对外字段要齐：来源、代码、输出、退出码、状态、耗时。"""

    row = SandboxExecution(
        id="e1",
        source="workflow",
        source_ref="n1",
        session_id="",
        workflow_run_id="r1",
        language="python",
        image="img",
        network_enabled=False,
        code="c",
        stdout="o",
        stderr="",
        exit_code=1,
        status="failed",
        error="boom",
        duration_ms=7,
    )
    wire = sandbox_service.execution_to_wire(row)
    for key in (
        "id",
        "source",
        "source_ref",
        "code",
        "stdout",
        "stderr",
        "exit_code",
        "status",
        "error",
        "duration_ms",
    ):
        assert key in wire
    assert wire["status"] == "failed"
    assert wire["exit_code"] == 1
