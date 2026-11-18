"""助理工具能力装配：技能、MCP、禁用名单、只读审批与联网搜索。

根助理、子助理与审批续跑共用这一套口径，避免三处装配逻辑各自漂移。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.skill_tools import (
    build_create_skill_tool,
    build_install_skill_from_url_tool,
    build_install_skill_from_zip_tool,
)
from app.adk_runtime.web_search_tool import build_web_search_tool
from app.request_context import session_id_var, user_id_var
from app.services.agent import agent_service
from app.services.agent.agent_service import ResolvedAgent
from app.services.mcp import mcp_service
from app.services.platform.web_search_service import resolve_deepseek_search_cred
from app.services.skills import skills_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentToolBundle:
    """一个助理在本回合可用的工具与限制。"""

    runtime_skills: list[Any]
    extra_tools: list[Any]
    confirm_tool_names: set[str]
    disallowed_tool_names: set[str]
    # 命中的审批规则动作（wire 名 → auto_approve/require_approval/block/escalate），
    # 供闸门绕过会话信任直接按规则判定
    rule_actions: dict[str, str]


async def build_agent_tool_bundle(
    db: AsyncSession,
    agent: ResolvedAgent,
    *,
    web_search_enabled: bool = False,
    allowed_tools_override: list[str] | None = None,
    direct_mcp_tools: bool = False,
) -> AgentToolBundle:
    """按助理配置装配技能、MCP 工具、审批与禁用名单。

    技能先解析再按「白名单 + 技能声明的工具」筛选 MCP Server：技能可以先建后改，
    只依赖保存助理时并入的白名单会漏掉后来新增的 MCP 工具。

    allowed_tools_override 供无人值守节点按本节点声明的 MCP 工具收窄检索目录：
    覆盖助理自身白名单，让 searchable 目录只含本节点要用的业务工具；None 时
    保持助理口径不变（主对话/子助理/审批/playground 均走此默认路径）。

    direct_mcp_tools=True 时把 allowed_tools_override 声明的 MCP 工具直接装配为
    可调用实体（不经 search→load），供已明确要用哪些工具的无人值守节点省掉发现轮次。
    仅无人值守节点传 True；主对话/子助理保持默认的延迟加载口径。
    """

    runtime_skills = await skills_service.resolve_skills(db, agent.skills)
    allowed_tools = (
        list(allowed_tools_override)
        if allowed_tools_override is not None
        else agent_service.effective_allowed_tools(agent, runtime_skills)
    )
    mcp_servers = await mcp_service.list_enabled_servers(db)
    mcp_servers = agent_service.filter_mcp_servers_by_tools(mcp_servers, allowed_tools)
    from app.services.mcp.tool_search_index import warm_vector_cache_from_db

    await warm_vector_cache_from_db(db)
    if direct_mcp_tools and allowed_tools_override:
        # 无人值守节点已知要用哪些工具：直接按名挂实体，跳过 search→load。
        extra_tools: list[Any] = list(
            await mcp_service.build_direct_mcp_toolsets(db, mcp_servers, allowed_tools)
        )
    else:
        extra_tools = list(
            await mcp_service.build_searchable_mcp_toolsets(
                db,
                mcp_servers,
                # 白名单是唯一口径：没勾选的工具不进检索目录，勾选的也仍需 search/load
                allowed_tools=allowed_tools,
            )
        )
    confirm_tool_names = mcp_service.confirm_tool_names_for_servers(mcp_servers)
    # 助理级禁用名单：命中的工具直接拒绝执行
    disallowed_tool_names = agent_service.agent_disallowed_tool_names(agent)
    # 技能要靠声明的工具工作，配置漂移（技能后加声明、助理已屏蔽）必须留痕
    drift = disallowed_tool_names & skills_service.declared_tools_of_skills(runtime_skills)
    if drift:
        logger.warning(
            "技能声明的工具被助理禁用名单拦截 agent=%s tools=%s",
            agent.name,
            sorted(drift),
        )
    # 助理级只读模式：该助理用到的 MCP 工具一律要人工确认
    if agent.permission_mode == "read-only":
        confirm_tool_names = confirm_tool_names | set(
            mcp_service.all_business_tool_names_for_servers(mcp_servers)
        )
        logger.info(
            "助理为只读模式，所有 MCP 工具需确认 agent=%s tools=%s",
            agent.name,
            len(confirm_tool_names),
        )
    if web_search_enabled:
        search_base, search_key = await resolve_deepseek_search_cred(db)
        extra_tools.append(build_web_search_tool(base_url=search_base, api_key=search_key))
        logger.info("已挂载 web_search agent=%s", agent.name)
    # 对话创建 / 安装技能：三个变更类工具随助理始终装配，走同一套人工审批闸门。
    # 归属信息从请求上下文取：会话路径的 user_id/session_id 已由鉴权与编排入口写入。
    raw_uid = user_id_var.get()
    owner_user_id = int(raw_uid) if raw_uid not in ("", "-") and raw_uid.isdigit() else None
    session_id = session_id_var.get()
    extra_tools.extend(
        [
            build_create_skill_tool(db, owner_user_id=owner_user_id),
            build_install_skill_from_url_tool(db, owner_user_id=owner_user_id),
            build_install_skill_from_zip_tool(
                db, session_id=session_id, owner_user_id=owner_user_id
            ),
        ]
    )
    logger.info(
        "已挂载技能创建/安装工具 agent=%s session_id=%s owner=%s",
        agent.name,
        session_id,
        owner_user_id,
    )
    # 平台助手：挂两个元工具代替大量 CRUD 接口。写操作进审批闸门，
    # GET 查询在审批回调里按目标 method 动态放行。
    from app.adk_runtime.platform_api_tools import (
        PLATFORM_AGENT_NAME,
        PLATFORM_CONFIRM_TOOL,
        build_platform_tools,
    )

    is_platform_agent = agent.name == PLATFORM_AGENT_NAME
    if is_platform_agent:
        extra_tools.extend(build_platform_tools())
        confirm_tool_names.add(PLATFORM_CONFIRM_TOOL)
        logger.info("已挂载平台助手元工具 agent=%s", agent.name)
    # 非 safe 的内置工具默认进闸（如 run_workflow）：默认要人工确认；
    # 审批规则 auto_approve 可以免审——风险定级与是否审批是两根轴
    from app.tools.catalog import BUILTIN_TOOL_SPECS

    confirm_tool_names = confirm_tool_names | {
        spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["risk"] != "safe"
    }
    # 审批规则（工具×助理×风险）：命中规则优先于 server 策略改写名单
    from app.services.chat.approval_rule_service import apply_rule_actions, resolve_actions

    tools_risk: dict[str, str | None] = {
        spec["name"]: spec["risk"] for spec in BUILTIN_TOOL_SPECS
    }
    for server in mcp_servers:
        for raw_name, risk in mcp_service.tool_risk_map(server).items():
            # tool_risk_map 键是原始工具名；闸门与规则匹配用 wire 名
            tools_risk[mcp_service.mcp_tool_wire_name(server.name, raw_name)] = risk
    decisions = await resolve_actions(db, agent_id=agent.name, tools=tools_risk)
    rule_actions = {name: d.action for name, d in decisions.items()}
    if rule_actions:
        confirm_tool_names, disallowed_tool_names = apply_rule_actions(
            confirm_tool_names, disallowed_tool_names, rule_actions
        )
        logger.info(
            "审批规则命中 agent=%s actions=%s",
            agent.name,
            {name: action for name, action in rule_actions.items()},
        )
    return AgentToolBundle(
        runtime_skills=runtime_skills,
        extra_tools=extra_tools,
        confirm_tool_names=confirm_tool_names,
        disallowed_tool_names=disallowed_tool_names,
        rule_actions=rule_actions,
    )
