"""ADK 代码执行器：把技能脚本送进沙盒。

ADK 的 SkillToolset 通过 code_executor 跑技能里的 scripts/。
默认实现 UnsafeLocalCodeExecutor 直接在宿主进程里起子进程执行，
技能脚本等于拿到了宿主机的读权限——技能可以来自用户 zip 导入，
这条路径不能留。

这里换成沙盒执行器：脚本在容器里跑，宿主机只拿到 stdout / stderr / 退出码。
ADK 的接口是同步的（execute_code），沙盒执行是异步的，
所以这里用 asyncio.run 把它跑进独立事件循环：调用发生在 ADK 的
工作线程里（技能工具在线程池中执行），那里没有可用的运行中事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from google.adk.code_executors.base_code_executor import BaseCodeExecutor
from google.adk.code_executors.code_execution_utils import (
    CodeExecutionInput,
    CodeExecutionResult,
)
from pydantic import Field
from typing_extensions import override

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

logger = logging.getLogger(__name__)


class SandboxCodeExecutor(BaseCodeExecutor):
    """在沙盒里执行代码的执行器。"""

    # 这个执行器无状态：每次执行都是独立容器，不保留上一次的状态。
    stateful: bool = Field(default=False, frozen=True, exclude=True)

    # 输入文件暂不支持：技能脚本的输入走 ADK 自己物化的文件，不走执行器。
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)

    # 单次执行的墙钟上限（秒）。ADK 会用这个值做超时兜底。
    timeout_seconds: int | None = Field(default=120)

    def __init__(self, **data: Any) -> None:
        if data.get("stateful"):
            raise ValueError("沙盒执行器不支持 stateful=True")
        if data.get("optimize_data_file"):
            raise ValueError("沙盒执行器不支持 optimize_data_file=True")
        super().__init__(**data)

    @override
    def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """执行代码并把结果交回 ADK。"""

        from app.services import sandbox_service

        code = code_execution_input.code
        logger.info("技能脚本进入沙盒 chars=%s", len(code))
        try:
            result = _run_sandbox(
                code,
                timeout=self.timeout_seconds,
                session_id=_session_id(invocation_context),
            )
        except sandbox_service.SandboxUnavailable as exc:
            # 沙盒不可用时绝不能退回宿主机执行：这里直接失败，
            # 让技能明确报「环境不可用」，而不是偷偷换个不安全的环境跑。
            logger.error("沙盒不可用，技能脚本未执行: %s", exc)
            return CodeExecutionResult(
                stdout="",
                stderr=f"沙盒环境不可用，技能脚本未执行: {exc}",
                output_files=[],
                exit_code=1,
            )
        except Exception as exc:  # noqa: BLE001 — 兜住容器层的异常
            logger.exception("沙盒执行失败")
            return CodeExecutionResult(
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                output_files=[],
                exit_code=1,
            )
        return CodeExecutionResult(
            stdout=result.stdout,
            stderr=result.stderr,
            output_files=[],
            exit_code=result.exit_code,
        )


def _session_id(invocation_context: Any) -> str:
    """取会话 id；取不到就留空，不让记录因为缺上下文而写不进去。"""

    session = getattr(invocation_context, "session", None)
    session_id = getattr(session, "id", "") if session is not None else ""
    return session_id if isinstance(session_id, str) else ""


def _run_sandbox(code: str, *, timeout: int | None, session_id: str) -> Any:
    """在独立事件循环里跑一次沙盒执行。

    ADK 的技能工具在线程池中调用 execute_code，那里没有运行中的事件循环，
    所以不能在调用方的循环里复用 asyncio.run 之外的调度方式。
    """

    from app.services import sandbox_service

    async def _main() -> Any:
        return await sandbox_service.run_code(
            code=code,
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_SKILL,
                session_id=session_id,
            ),
            timeout_seconds=timeout,
        )

    return asyncio.run(_main())
