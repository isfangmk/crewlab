"""技能脚本走沙盒执行器。

覆盖范围：
  - 技能脚本在容器里跑，stdout 与退出码如实交回 ADK；
  - 脚本报错时 stderr 与非零退出码要如实交回；
  - 沙盒不可用时必须失败，绝不能退回宿主机执行；
  - 执行记录要落一条 skill 来源的记录。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adk_runtime.sandbox_code_executor import SandboxCodeExecutor, _session_id
from app.services import sandbox_service


def _sandbox_available() -> bool:
    """沙盒是否可用；不可用就跳过用例。"""

    try:
        sandbox_service._sandbox_settings()
        sandbox_service._docker_client()
    except sandbox_service.SandboxUnavailable:
        return False
    return True


@pytest.fixture
def db_sessions():
    """把落库用的 session 换成本用例自己的。

    技能执行器在自己的事件循环里跑（ADK 的接口是同步的），
    模块级 engine 的连接池跨事件循环复用会报 Event loop is closed，
    所以这里造一个用例自己的 engine 顶上。
    """

    import asyncio

    from app.config import get_settings
    from app.db import SessionLocal as ModuleSessionLocal

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with patch.object(sandbox_service, "SessionLocal", maker):
            with patch("app.db.SessionLocal", maker):
                yield maker
    finally:
        asyncio.new_event_loop().run_until_complete(engine.dispose())


def _invocation_context(session_id: str = "sess_skill_test"):
    """造一个最小的 invocation context。"""

    return SimpleNamespace(session=SimpleNamespace(id=session_id))


def _code_input(code: str):
    """造一个 CodeExecutionInput。"""

    from google.adk.code_executors.code_execution_utils import CodeExecutionInput

    return CodeExecutionInput(code=code, input_files=[], execution_id="exec-1")


def test_executor_rejects_stateful_and_optimize() -> None:
    """沙盒执行器不支持有状态与输入文件优化：这两个都会绕过容器语义。"""

    with pytest.raises(ValueError, match="不支持 stateful"):
        SandboxCodeExecutor(stateful=True)
    with pytest.raises(ValueError, match="不支持 optimize_data_file"):
        SandboxCodeExecutor(optimize_data_file=True)


def test_session_id_extraction_is_forgiving() -> None:
    """取不到会话 id 时留空，不能让记录因为缺上下文而写不进去。"""

    assert _session_id(_invocation_context("sess_1")) == "sess_1"
    assert _session_id(SimpleNamespace(session=None)) == ""
    assert _session_id(SimpleNamespace()) == ""


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
def test_skill_script_runs_in_sandbox(db_sessions) -> None:
    """技能脚本在容器里跑，输出如实交回。"""

    executor = SandboxCodeExecutor()
    result = executor.execute_code(
        _invocation_context(), _code_input("print('skill-ok')")
    )
    assert "skill-ok" in result.stdout
    assert result.exit_code == 0


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
def test_skill_script_failure_is_reported(db_sessions) -> None:
    """脚本报错时 stderr 与非零退出码要如实交回，不能当成功。"""

    executor = SandboxCodeExecutor()
    result = executor.execute_code(
        _invocation_context(),
        _code_input("import sys; sys.stderr.write('bad\\n'); raise SystemExit(2)"),
    )
    assert result.exit_code == 2
    assert "bad" in result.stderr


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
def test_skill_execution_is_recorded(db_sessions) -> None:
    """技能脚本的执行要落一条 skill 来源的记录。"""

    import asyncio

    from app.models_orm.sandbox import SandboxExecution

    executor = SandboxCodeExecutor()
    executor.execute_code(
        _invocation_context("sess_skill_record"), _code_input("print(1)")
    )

    async def _read() -> list[object]:
        async with db_sessions() as db:
            rows = (
                await db.execute(
                    select(SandboxExecution).where(SandboxExecution.source == "skill")
                )
            ).scalars().all()
            return list(rows)

    rows = asyncio.new_event_loop().run_until_complete(_read())
    assert len(rows) >= 1
    assert rows[-1].source == "skill"
    assert rows[-1].session_id == "sess_skill_record"


def test_sandbox_unavailable_never_falls_back_to_host() -> None:
    """沙盒不可用时必须明确失败，不能退回宿主机执行。

    退回宿主机等于把这套隔离整个作废，所以这里要断言「失败」，
    而不是断言「还能跑出结果」。
    """

    executor = SandboxCodeExecutor()
    with patch.object(
        sandbox_service,
        "run_code",
        side_effect=sandbox_service.SandboxUnavailable("Docker 未就绪"),
    ):
        result = executor.execute_code(
            _invocation_context(), _code_input("print('should not run')")
        )
    assert result.exit_code == 1
    assert "沙盒环境不可用" in result.stderr
    assert "should not run" not in result.stdout
