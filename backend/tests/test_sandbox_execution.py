"""沙盒真实执行：跑真容器，验隔离与记录。

覆盖范围：
  - 代码在容器里跑，输出与退出码如实回来；
  - 失败代码的 stderr 与退出码如实回来；
  - 每次执行都落一条记录，来源与上下文齐全；
  - 容器网络默认关闭：沙盒里的代码访问不到外网；
  - 输出捕获不依赖 exec 的 socket（连续多次执行都要拿到输出）。
这些用例依赖 Docker 环境；环境不可用时跳过，不假装通过。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services import sandbox_service


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


def _sandbox_available() -> bool:
    """沙盒是否可用；不可用就跳过用例。"""

    try:
        sandbox_service._sandbox_settings()
        sandbox_service._docker_client()
    except sandbox_service.SandboxUnavailable:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _sandbox_available(), reason="沙盒环境不可用（Docker 未就绪）"
)


@pytest.mark.asyncio
async def test_code_runs_in_container_and_returns_output(db_sessions) -> None:
    """代码在容器里跑，stdout 要如实回来。"""

    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code="print(6 * 7)",
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test",
            ),
        )
    assert result.status == sandbox_service.STATUS_SUCCEEDED
    assert "42" in result.stdout
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_failing_code_reports_stderr_and_exit_code(db_sessions) -> None:
    """失败的代码要留下 stderr 与非零退出码，不能当成成功。"""

    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code="import sys; sys.stderr.write('boom\\n'); raise SystemExit(3)",
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test",
            ),
        )
    assert result.status == sandbox_service.STATUS_FAILED
    assert result.exit_code == 3
    assert "boom" in result.stderr


@pytest.mark.asyncio
async def test_network_is_blocked_by_default(db_sessions) -> None:
    """默认断网：沙盒里的代码不能访问外网。

    断网是这套隔离的核心承诺，配置说断网而实际能联网就等于承诺失效。
    """

    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=5)\n"
        "    print('NET_OK')\n"
        "except Exception:\n"
        "    print('NET_BLOCKED')\n"
    )
    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code=code,
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test",
            ),
        )
    assert result.status == sandbox_service.STATUS_SUCCEEDED
    assert "NET_BLOCKED" in result.stdout
    assert "NET_OK" not in result.stdout


@pytest.mark.asyncio
async def test_execution_is_recorded_with_context(db_sessions) -> None:
    """执行记录要带上来源、代码、输出与耗时。"""

    from app.models_orm.sandbox import SandboxExecution

    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code="print('recorded')",
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_WORKFLOW,
                source_ref="节点-测试",
                workflow_run_id="run-test-uid",
            ),
        )
        row = await db.get(SandboxExecution, result.execution_id)
    assert row is not None
    assert row.source == "workflow"
    assert row.source_ref == "节点-测试"
    assert row.workflow_run_id == "run-test-uid"
    assert "recorded" in row.stdout
    assert row.status == "succeeded"
    assert row.duration_ms > 0
    assert row.image != ""


@pytest.mark.asyncio
async def test_output_capture_is_reliable_across_runs(db_sessions) -> None:
    """连续多次执行都要拿到输出。

    docker 的 exec 偶发在拿到数据帧之前就返回 EOF，输出会整段丢失；
    输出改走容器内文件再读回来之后不该再出现「跑成功了但没输出」。
    """

    for i in range(6):
        async with db_sessions() as db:
            result = await sandbox_service.run_code(
                code=f"print('run-{i}')",
                ctx=sandbox_service.SandboxContext(
                    source=sandbox_service.SOURCE_MANUAL,
                    source_ref="test",
                ),
            )
        assert f"run-{i}" in result.stdout, f"第 {i} 次执行输出丢失"
        assert result.status == sandbox_service.STATUS_SUCCEEDED


@pytest.mark.asyncio
async def test_sandbox_cannot_read_host_files(db_sessions) -> None:
    """沙盒里读不到宿主机文件：隔离靠容器，不是靠禁用内置函数。"""

    code = (
        "import os\n"
        "print('HOST_DIR' if os.path.exists('/mnt/c/Users') "
        "or os.path.exists('/mnt/d') else 'ISOLATED')\n"
    )
    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code=code,
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test",
            ),
        )
    assert result.status == sandbox_service.STATUS_SUCCEEDED
    assert "ISOLATED" in result.stdout
    assert "HOST_DIR" not in result.stdout


@pytest.mark.asyncio
async def test_record_keeps_real_start_and_finish(db_sessions) -> None:
    """起止时间必须夹住真实执行：两者相同说明 started_at 记成了结束时刻。

    列表按 started_at 排序与筛选，记错会让「某段时间跑了什么」查不准。
    """

    from app.models_orm.sandbox import SandboxExecution

    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code="import time; time.sleep(1)",
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test-start-at",
            ),
        )
        row = await db.get(SandboxExecution, result.execution_id)

    assert row is not None
    assert row.started_at is not None
    assert row.finished_at is not None
    assert row.finished_at > row.started_at
    # 墙钟差与记录的耗时口径一致（允许 1 秒误差：容器启动开销也计在 duration 里）
    span = (row.finished_at - row.started_at).total_seconds() * 1000
    assert abs(span - row.duration_ms) < 1000


@pytest.mark.asyncio
async def test_large_output_is_capped(db_sessions) -> None:
    """超长输出要在读取阶段就被截断，落库时再截一次。

    容器磁盘没有配额，代码可以一直打印；整份读进内存会把 API 进程拖垮。
    """

    from app.models_orm.sandbox import SandboxExecution

    printed = 400_000
    async with db_sessions() as db:
        result = await sandbox_service.run_code(
            code=f"print('x' * {printed})",
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="test-truncate",
            ),
        )
        row = await db.get(SandboxExecution, result.execution_id)

    assert result.status == sandbox_service.STATUS_SUCCEEDED
    assert len(result.stdout) < printed
    assert "已截断" in result.stdout
    assert row is not None
    assert len(row.stdout) <= sandbox_service.OUTPUT_LIMIT + 100
    assert "已截断" in row.stdout
