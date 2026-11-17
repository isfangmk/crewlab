"""画布任务节点的缺省执行器：按节点声明自足执行无人值守任务。

系统编排（任务图）的任务节点由编排服务注入执行上下文（节点行、事件通道、
停止信号）；从画布直接运行时没有这些注入，走这里——用平台默认模型装配
只读 agent，按节点声明收窄工具面，产出摘要写回共享状态。工具策略复用
services/execution 共享层，不依赖任何旧 investigation/plan 代码。
预算走 WorkerBudget 单一源头缺省；停止接工作流运行自己的停止信号。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from google.adk.agents import LlmAgent

from app.adk_runtime.agent_factory import build_chat_agent
from app.adk_runtime.headless import WorkerBudget, run_headless_turn
from app.adk_runtime.toolset import build_knowledge_tools
from app.db import SessionLocal
from app.services.execution.node_runner import build_node_persona
from app.services.execution.tool_policy import (
    FOCUS_GUIDANCE,
    READONLY_TOOL_NAMES,
    build_readonly_builtin_tools,
    build_readonly_tool_gate,
    filter_tools_by_focus,
    normalize_tool_focus,
)

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

    spec = node.task
    if spec is None:
        raise RuntimeError(f"任务节点缺少执行参数: {node.id}")

    async def publish(_event: dict[str, Any]) -> None:
        return

    try:
        result = await _run_spec(
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


async def _build_canvas_agent(
    *, name: str, target: str, instruction: str, tools: tuple[str, ...]
) -> LlmAgent:
    """用平台默认模型装配只读 agent（无会话，工具策略与任务图一致）。"""

    from app.services.platform import app_settings_service, llm_config

    async with SessionLocal() as db:
        provider_id, model_name = await llm_config.get_default_selection(db)
        provider = await llm_config.get_provider(db, provider_id)
        runtime_cfg = await app_settings_service.get_agent_runtime(db)

    persona = f"{build_node_persona(name, instruction)}\n{FOCUS_GUIDANCE}"
    agent_tools = build_readonly_builtin_tools(READONLY_TOOL_NAMES)
    agent_tools.extend(build_knowledge_tools(None))
    agent_tools = filter_tools_by_focus(agent_tools, normalize_tool_focus(tools or None))

    return build_chat_agent(
        provider_id=provider.provider_id,
        model=model_name,
        base_url=provider.base_url,
        api_key=provider.api_key,
        agent_name=f"canvas-{name}",
        agent_description=f"无人值守执行：{target}",
        persona_instruction=persona,
        thinking_enabled=False,
        model_prefix=provider.model_prefix,
        provider_extra_body=llm_config.provider_extra_body(provider),
        extra_tools=agent_tools,
        llm_timeout_seconds=int(runtime_cfg["llm_timeout_seconds"]),
        confirm_tool_names=set(),
        disallowed_tool_names=set(),
        max_turns=WorkerBudget().max_steps,
        knowledge_scope=None,
        skills=[],
        include_builtin_tools=False,
        include_ask_user=False,
        include_followups=False,
        before_tool_callback=build_readonly_tool_gate(None, allow=set(READONLY_TOOL_NAMES)),
    )


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


    budget = WorkerBudget()
    agent = await _build_canvas_agent(
        name=name, target=target, instruction=instruction, tools=tools
    )
    prompt = "\n".join(
        [
            f"任务：{name}",
            f"目标：{target}",
            f"要求：{instruction}",
        ]
    )

    async def _log(line: str, tool: str = "") -> None:
        await publish(
            {
                "event": "canvas_task_log",
                "run_uid": run_uid,
                "line": line,
                "tool": tool,
            }
        )

    return await run_headless_turn(
        agent=agent,
        app_name="canvas_task",
        prompt=prompt,
        budget=budget,
        log=_log,
        stop_check=stop_check,
        opening=f"开始执行：{name}",
        closing="任务完成",
    )
