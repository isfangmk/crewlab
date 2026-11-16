"""把内置工具装配成 ADK 可挂载的 FunctionTool 列表。

只做装配：工具行为在 app/tools 各主题模块，目录与注册表在 app/tools/catalog。
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from app.services.knowledge.knowledge_scope import KnowledgeScope
from app.tools.catalog import (
    builtin_group_names,
    builtin_tool_registry,
    tool_impl,
    tool_param_specs,
)
from app.tools.knowledge import query_knowledge, scoped_query_knowledge
from app.tools.user_input import ASK_USER_TOOL_NAME

logger = logging.getLogger(__name__)

__all__ = [
    "build_knowledge_tools",
    "build_builtin_tools",
    "builtin_tool_registry",
    "tool_param_specs",
]


def build_knowledge_tools(scope: KnowledgeScope | None = None) -> list[FunctionTool]:
    """供 SkillToolset.additional_tools 挂载；scope 为空时使用全库检索。

    只返回知识检索工具：ADK 的 additional_tools 要等技能在元数据里声明后
    才注入，代码解释器/记忆/工作流这类始终可用的能力直接挂在 Agent 上。
    """

    if scope is None or scope.is_empty:
        return [FunctionTool(query_knowledge)]
    logger.info(
        "装配带知识范围的 query_knowledge paths=%s doc_ids=%s",
        list(scope.paths),
        len(scope.doc_ids),
    )
    return [FunctionTool(scoped_query_knowledge(scope))]


def build_builtin_tools(*, include_ask_user: bool = True) -> list[FunctionTool]:
    """始终装配的内置工具组：代码解释器、记忆、工作流、并行排查、规划执行与澄清交互。

    ADK 的 SkillToolset.additional_tools 只有在技能声明后才注入；这些能力
    不依赖外部凭证且对话随时可用，所以直接作为 Agent 工具挂载。
    include_ask_user=False 用于子 Agent：被委派的子任务无人值守，
    澄清提问只能出现在与用户直接对话的根 Agent 上；
    并行排查派发与计划创建同理——worker/步骤无人值守，不能往下再派。
    """

    names = builtin_group_names(
        "workflow", "code", "memory", "interaction", "investigation", "planning"
    )
    if not include_ask_user:
        names = [
            name
            for name in names
            if name != ASK_USER_TOOL_NAME
            and name
            not in (
                "dispatch_investigation",
                "get_investigation",
                "create_plan",
                "get_plan",
                "update_plan_steps",
            )
        ]
    return [FunctionTool(tool_impl(name)) for name in names]
