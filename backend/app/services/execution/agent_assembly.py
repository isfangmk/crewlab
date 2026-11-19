"""无人值守 agent 装配：任务图节点与收尾汇总的唯一装配入口。

集中处理：会话/模型解析、工具包、知识范围、web 搜索、只读闸门、
人设与工具聚焦。唯一消费者是新架构 taskgraph，旧装配代码删除后无残留。

关键业务路径打日志：装配入口记录会话/模型/工具数，便于排查与发现冗余。
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent

from app.adk_runtime.agent_factory import build_chat_agent
from app.adk_runtime.headless import WorkerBudget
from app.adk_runtime.toolset import build_knowledge_tools
from app.db import SessionLocal
from app.services.execution.tool_policy import (
    ANALYST_TOOL_NAMES,
    FOCUS_GUIDANCE,
    READONLY_TOOL_NAMES,
    ToolFocus,
    build_readonly_builtin_tools,
    build_readonly_tool_gate,
    filter_tools_by_focus,
    normalize_tool_focus,
)
from app.services.knowledge.knowledge_scope import KnowledgeScope

logger = logging.getLogger(__name__)


async def assemble_agent(
    *,
    session_id: str,
    name: str,
    target: str,
    instruction: str,
    budget: WorkerBudget,
    tools: tuple[str, ...] = (),
    skills: tuple[str, ...] | None = None,
    persona: str | None = None,
    agent_id: str = "",
    allow_run_python: bool = False,
) -> LlmAgent:
    """装配一个无人值守只读 agent。

    - 内置只读工具 + 知识检索 + 继承的 MCP（禁写闸门拦）；
    - persona 缺省用节点人设，汇总传自定义人设；agent_id 指定时改用该助理人设；
    - allow_run_python=True 时把 run_python 加入只读白名单并免闸门启发式；
    - tools 非空则按名收窄挂载面（"__none__" 表示不挂任何工具）；其中 mcp__*
      业务工具名作为 MCP 检索目录白名单，让节点能 search→load 真正需要的工具；
    - skills 非 None 时按声明的 key 装配技能（空元组 = 不挂任何技能），覆盖助理默认；
      None 时保持原行为：指定 agent_id 用该助理技能，否则不挂技能；
    - agent_id 非空时复用该助理的人设/MCP 白名单/技能/知识范围，
      模型仍走会话口径，只读闸门照常生效。

    会话/模型/工具在单一 SessionLocal 里解析，装配在会话外完成，避免长事务。
    """

    from app.adk_runtime.web_search_tool import build_session_web_search_tool
    from app.models.chat import ChatSession
    from app.services.agent import agent_service
    from app.services.agent.agent_tool_service import build_agent_tool_bundle
    from app.services.platform import app_settings_service, llm_config
    from app.services.platform.llm_config import resolve_model

    logger.info(
        "装配无人值守 agent session_id=%s name=%s agent_id=%s tools=%s skills=%s allow_python=%s",
        session_id,
        name,
        agent_id,
        list(tools),
        list(skills) if skills is not None else None,
        allow_run_python,
    )

    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            raise LookupError(f"会话不存在: {session_id}")
        if not session.agent_id:
            raise LookupError("会话未绑定 agent_id")
        # 模型口径来自会话绑定助理；工具/人设口径默认同它，指定 agent_id 时改用目标助理。
        resolved_agent = await agent_service.get_agent(db, session.agent_id)
        # 指定助理不存在须显式失败，不静默回退到根助理（契约不符显式失败）。
        tool_agent = (
            await agent_service.get_agent(db, agent_id)
            if agent_id != ""
            else resolved_agent
        )
        use_model = session.model
        if resolved_agent.model != "":
            use_model = resolved_agent.model
        llm = await resolve_model(db, session.provider, use_model)
        # MCP 工具白名单口径：
        # - 节点声明了 mcp__* 工具（无论是否指定 agent_id）：按声明 direct 直挂实体，
        #   省掉 search→load 往返；agent_id 节点仍复用该助理的人设/技能/知识范围，
        #   但工具边界以节点规划为准（图生成时已明确要用哪些工具）。
        # - 未声明 mcp__* 工具：未指定 agent_id 按 tools 收窄内置工具，
        #   指定 agent_id 沿用该助理自身白名单的 searchable 目录（兜底）。
        mcp_focus = [t for t in tools if t.startswith("mcp__")]
        if mcp_focus:
            bundle = await build_agent_tool_bundle(
                db,
                tool_agent,
                allowed_tools_override=mcp_focus,
                direct_mcp_tools=True,
            )
            # focus 保留完整声明：filter_tools_by_focus 按 mcp__<server> 命名空间前缀
            # 保留直挂 toolset，同时按名精确匹配保留声明的内置工具。
            effective_focus = tools or None
        else:
            bundle = await build_agent_tool_bundle(db, tool_agent)
            effective_focus = None if agent_id != "" else (tools or None)
        runtime_cfg = await app_settings_service.get_agent_runtime(db)
        scope = (
            tool_agent.knowledge_scope
            if isinstance(tool_agent.knowledge_scope, KnowledgeScope)
            else None
        )
        # 指定助理时用其人设；critical_reminder 一并并入，保证口径与主对话一致。
        effective_persona = persona
        if agent_id != "" and effective_persona is None:
            effective_persona = tool_agent.system_prompt
            if tool_agent.critical_reminder:
                effective_persona = (
                    f"{effective_persona}\n{tool_agent.critical_reminder}"
                )
        # build_chat_agent 的 skills 要已解析的技能对象。
        # 节点显式声明 skills 时按 key 解析（空 = 不挂），覆盖该助理默认技能；
        # 未声明（None）保持原行为：指定 agent_id 用该助理技能，否则不挂技能。
        # 未知 key 由 resolve_skills 显式失败，不静默忽略。
        if skills is not None:
            from app.services.skills.skills_service import resolve_skills

            effective_skills = await resolve_skills(db, list(skills))
        else:
            effective_skills = bundle.runtime_skills if agent_id != "" else []
        extra_tools = list(bundle.extra_tools)
        web_tool = await build_session_web_search_tool(db, session)
        if web_tool is not None:
            extra_tools.append(web_tool)

        provider_id = llm.provider_id
        base_url = llm.base_url
        api_key = llm.api_key
        model_prefix = llm.model_prefix
        provider_extra_body = llm_config.provider_extra_body(llm)
        disallowed = set(bundle.disallowed_tool_names or ())
        llm_timeout = int(runtime_cfg["llm_timeout_seconds"])
        provider_label = session.provider

    readonly_names = ANALYST_TOOL_NAMES if allow_run_python else READONLY_TOOL_NAMES

    agent = _build_agent(
        provider_id=provider_id,
        model=use_model,
        base_url=base_url,
        api_key=api_key,
        name=name,
        target=target,
        instruction=instruction,
        extra_tools=extra_tools,
        skills=effective_skills,
        knowledge_scope=scope,
        model_prefix=model_prefix,
        provider_extra_body=provider_extra_body,
        disallowed_tool_names=disallowed,
        budget=budget,
        llm_timeout_seconds=llm_timeout,
        persona=effective_persona,
        readonly_names=readonly_names,
        tool_focus=effective_focus,
    )
    logger.info(
        "装配无人值守 agent 完成 session_id=%s name=%s model=%s/%s tools_mounted=%s",
        session_id,
        name,
        provider_label,
        use_model,
        len(getattr(agent, "tools", []) or []),
    )
    return agent


def _build_agent(
    *,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    name: str,
    target: str,
    instruction: str,
    extra_tools: list[Any] | None,
    skills: list[Any],
    knowledge_scope: KnowledgeScope | None,
    model_prefix: str,
    provider_extra_body: dict[str, Any] | None,
    disallowed_tool_names: set[str],
    budget: WorkerBudget,
    llm_timeout_seconds: int,
    persona: str | None,
    readonly_names: tuple[str, ...],
    tool_focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | None,
) -> LlmAgent:
    """真正装配 LlmAgent：工具收窄 + 人设 + 只读闸门。"""

    if persona is None:
        from app.services.execution.node_runner import build_node_persona

        persona = build_node_persona(name, instruction)
    if instruction.strip():
        persona = f"{persona}\n\n本节点要求：{instruction.strip()}"
    persona = f"{persona}\n{FOCUS_GUIDANCE}"

    tools = build_readonly_builtin_tools(readonly_names)
    tools.extend(build_knowledge_tools(knowledge_scope))
    if extra_tools:
        tools.extend(extra_tools)
    tools = filter_tools_by_focus(tools, normalize_tool_focus(tool_focus))

    return build_chat_agent(
        provider_id=provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        agent_name=f"taskgraph-{name}",
        agent_description=f"无人值守执行：{target}",
        persona_instruction=persona,
        thinking_enabled=False,
        model_prefix=model_prefix,
        provider_extra_body=provider_extra_body,
        extra_tools=tools,
        llm_timeout_seconds=llm_timeout_seconds,
        confirm_tool_names=set(),
        disallowed_tool_names=disallowed_tool_names,
        max_turns=budget.max_steps,
        knowledge_scope=knowledge_scope,
        skills=skills,
        include_builtin_tools=False,
        include_ask_user=False,
        include_followups=False,
        before_tool_callback=build_readonly_tool_gate(
            disallowed_tool_names, allow=set(readonly_names)
        ),
    )
