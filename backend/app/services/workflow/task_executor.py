"""画布任务节点的缺省执行器：按节点声明自足执行无人值守任务。

系统编排（排查/计划）的任务节点由服务注入执行上下文（worker 行、事件通道、
停止信号）；从画布直接运行时没有这些注入，走这里——用平台默认模型装配
只读 worker，按节点声明收窄工具面，产出摘要写回共享状态。预算走
WorkerBudget 单一源头缺省；停止接工作流运行自己的停止信号。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.db import SessionLocal

logger = logging.getLogger(__name__)

Publish = Callable[[dict[str, Any]], Any]


def build_canvas_task_runner(*, run_uid: str) -> Callable[[Any], Any]:
    """构造画布任务节点的缺省执行器：节点定义进来，结果摘要出去。"""

    async def runner(node: Any) -> dict[str, Any]:
        return await run_canvas_task(node=node, run_uid=run_uid)

    return runner


async def run_canvas_task(*, node: Any, run_uid: str) -> dict[str, Any]:
    """按任务节点声明执行一次无人值守任务（无会话、无派发行）。"""

    from app.services.workflow import run_control
    from app.services.workflow import task_executor as _self

    spec = node.task
    if spec is None:
        raise RuntimeError(f"任务节点缺少执行参数: {node.id}")

    async def publish(_event: dict[str, Any]) -> None:
        # 画布任务没有派发事件通道：过程日志不落事件，结论进节点产出
        return

    try:
        result = await _self._run_spec(
            name=str(node.label or node.id),
            target=spec.target,
            instruction=spec.instruction,
            tools=tuple(spec.tools),
            run_uid=run_uid,
            publish=publish,
            stop_check=lambda: run_control.is_stopped(run_uid),
        )
    except Exception as exc:  # noqa: BLE001 - 装配失败也记成任务失败
        logger.exception("画布任务执行失败 node=%s run_uid=%s", node.id, run_uid)
        return {"status": "failed", "conclusion": "", "error": str(exc)}
    return {
        "status": result.status,
        "conclusion": result.conclusion,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


async def _run_spec(
    *,
    name: str,
    target: str,
    instruction: str,
    tools: tuple[str, ...],
    run_uid: str,
    publish: Publish,
    stop_check: Callable[[], bool],
):
    """按声明装配并跑完一个无人值守任务（默认模型 + 只读工具面）。"""

    from app.adk_runtime.headless import WorkerBudget
    from app.services.investigation import worker_runner
    from app.services.platform import app_settings_service, llm_config

    async with SessionLocal() as db:
        provider_id, model_name = await llm_config.get_default_selection(db)
        provider = await llm_config.get_provider(db, provider_id)
        runtime_cfg = await app_settings_service.get_agent_runtime(db)

    agent = worker_runner.build_worker_agent(
        provider_id=provider.provider_id,
        model=model_name,
        base_url=provider.base_url,
        api_key=provider.api_key,
        name=name,
        target=target,
        instruction=instruction,
        extra_tools=[],
        skills=[],
        knowledge_scope=None,
        model_prefix=provider.model_prefix,
        provider_extra_body=llm_config.provider_extra_body(provider),
        disallowed_tool_names=set(),
        budget=WorkerBudget(),
        llm_timeout_seconds=int(runtime_cfg["llm_timeout_seconds"]),
        # 声明了所需工具就收窄挂载面，不声明挂默认全量只读集
        tool_focus=tools or None,
    )
    return await worker_runner.run_worker(
        agent=agent,
        dispatch_uid=run_uid,
        worker_uid=f"{run_uid}:{name}",
        name=name,
        target=target,
        question=target,
        instruction=instruction,
        budget=WorkerBudget(),
        publish=publish,
        stop_check=stop_check,
    )
