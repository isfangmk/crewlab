"""工作流代码节点走沙盒的行为。

覆盖范围：
  - 默认执行模式是沙盒，代码节点在容器里跑出结果；
  - 沙盒里读到的状态键要登记成节点的输入；
  - 代码报错时要抛出可读的错误，不能当成成功；
  - restricted 模式仍可用（纯计算，不走容器）；
  - execution_mode 与 libraries 的入参校验。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adk_runtime.workflow_node_impl import CodeNode
from app.adk_runtime.workflow_nodes import RunContext
from app.schemas.workflow import WorkflowDefinition, WorkflowNode
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
async def db_sessions():
    """用例自己的会话：用来回读执行记录。

    沙盒服务落库用的是它自己那条 NullPool 引擎，不与用例共享连接，
    所以这里不需要打补丁，直接给用例一个能读库的会话。
    """

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def _make_node(code: str, **kwargs) -> CodeNode:
    """造一个代码节点。"""

    start_def = WorkflowNode(
        id="start_1",
        kind="start",
        variables=[{"name": "a", "type": "number"}],
    )
    node_def = WorkflowNode(
        id="code_1",
        kind="code",
        code=code,
        **kwargs,
    )
    definition = WorkflowDefinition(
        nodes=[start_def, node_def],
        edges=[{"id": "e1", "source": "start_1", "target": "code_1"}],
    )
    return CodeNode(
        node=node_def,
        run_ctx=RunContext(definition=definition, db=None),
        name="code_1",
    )


def test_default_execution_mode_is_sandbox() -> None:
    """代码节点默认走沙盒。

    画布上的代码是不可信输入，默认在宿主进程里跑等于把宿主交给它。
    """

    node_def = WorkflowNode(
        id="code_1", kind="code", code="def run(state):\n    return 1\n"
    )
    assert node_def.execution_mode == "sandbox"
    assert node_def.libraries == []


def test_libraries_rejects_unsafe_package_names() -> None:
    """libraries 会拼进容器里的 pip 命令，包名必须先校验。"""

    with pytest.raises(ValueError, match="非法的 pip 包名"):
        WorkflowNode(
            id="code_1",
            kind="code",
            code="def run(state):\n    return 1\n",
            libraries=["requests; rm -rf /"],
        )

    with pytest.raises(ValueError, match="非法的 pip 包名"):
        WorkflowNode(
            id="code_1",
            kind="code",
            code="def run(state):\n    return 1\n",
            libraries=["../../etc/passwd"],
        )


def test_libraries_rejects_too_many() -> None:
    """库数量要有上限，避免一次执行装一堆包把容器拖垮。"""

    with pytest.raises(ValueError, match="libraries 最多"):
        WorkflowNode(
            id="code_1",
            kind="code",
            code="def run(state):\n    return 1\n",
            libraries=[f"pkg{i}" for i in range(21)],
        )


def test_non_code_node_rejects_code_exec_options() -> None:
    """非代码节点不能设执行模式或依赖库，否则填了却不生效。"""

    with pytest.raises(ValueError, match="不能设置 code_exec"):
        WorkflowNode(
            id="start_1",
            kind="start",
            variables=[{"name": "x", "type": "string"}],
            execution_mode="restricted",
        )

    with pytest.raises(ValueError, match="不能设置 code_exec"):
        WorkflowNode(
            id="start_2",
            kind="start",
            variables=[{"name": "x", "type": "string"}],
            libraries=["requests"],
        )


@pytest.mark.asyncio
async def test_restricted_mode_runs_in_process() -> None:
    """restricted 模式在进程内跑，不碰容器。"""

    node = _make_node(
        "def run(state):\n    return state['a'] + 1\n", execution_mode="restricted"
    )
    result = await node._run_code({"a": 1})
    assert result == 2


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
@pytest.mark.asyncio
async def test_sandbox_mode_returns_result(db_sessions) -> None:
    """沙盒模式：代码在容器里跑，返回值要如实回来。"""

    node = _make_node("def run(state):\n    return {'total': state['a'] + 1}\n")
    async with db_sessions() as db:
        node.run_ctx.db = db
        result = await node._run_code({"a": 1})
    assert result == {"total": 2}


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
@pytest.mark.asyncio
async def test_sandbox_mode_records_reads(db_sessions) -> None:
    """沙盒模式：代码读到的状态键要登记成节点输入。"""

    node = _make_node("def run(state):\n    return state['used'] * 2\n")
    async with db_sessions() as db:
        node.run_ctx.db = db
        await node._run_code({"used": 3, "unused": 9})
    assert node.input_snapshot({"used": 3, "unused": 9}) == {"used": 3}


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
@pytest.mark.asyncio
async def test_sandbox_mode_raises_on_code_error(db_sessions) -> None:
    """代码报错要抛出可读的错误，不能当成成功。"""

    node = _make_node("def run(state):\n    raise ValueError('坏了')\n")
    async with db_sessions() as db:
        node.run_ctx.db = db
        with pytest.raises(RuntimeError, match="代码节点执行失败"):
            await node._run_code({})


@pytest.mark.skipif(not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）")
@pytest.mark.asyncio
async def test_sandbox_mode_records_execution(db_sessions) -> None:
    """代码节点的沙盒执行要落一条 workflow 来源的记录。"""

    from app.models_orm.sandbox import SandboxExecution

    node = _make_node("def run(state):\n    return 1\n")
    node.run_ctx.run_uid = "run_uid_test"
    async with db_sessions() as db:
        node.run_ctx.db = db
        # 先清掉上轮遗留的记录：断言的是「这一次执行留下了一行」
        await db.execute(
            delete(SandboxExecution).where(
                SandboxExecution.workflow_run_id == "run_uid_test"
            )
        )
        await db.commit()
        await node._run_code({})
        rows = (
            await db.execute(
                select(SandboxExecution).where(
                    SandboxExecution.workflow_run_id == "run_uid_test"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "workflow"
    assert rows[0].source_ref == "code_1"
