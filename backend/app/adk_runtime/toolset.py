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
    """知识检索工具：作为 Agent 常驻工具挂载，scope 为空时使用全库检索。

    代码解释器/记忆/工作流这类能力同样常驻；知识检索带每个助理不同的知识范围，
    故单独构造并并入 build_builtin_tools。
    """

    if scope is None or scope.is_empty:
        return [FunctionTool(query_knowledge)]
    logger.info(
        "装配带知识范围的 query_knowledge paths=%s doc_ids=%s",
        list(scope.paths),
        len(scope.doc_ids),
    )
    return [FunctionTool(scoped_query_knowledge(scope))]


def build_builtin_tools(
    *, include_ask_user: bool = True, knowledge_scope: KnowledgeScope | None = None
) -> list[FunctionTool]:
    """始终装配的内置工具组：代码解释器、记忆、工作流、任务图执行、知识检索与澄清交互。

    这些能力不依赖技能声明，直接作为 Agent 工具挂载。
    knowledge_scope 注入给常驻的 query_knowledge（空 = 全库检索）。
    include_ask_user=False 用于子 Agent：被委派的子任务无人值守，
    澄清提问只能出现在与用户直接对话的根 Agent 上；
    任务图提交同理——节点无人值守，不能往下再开图。
    """

    names = builtin_group_names("workflow", "code", "memory", "interaction", "taskgraph")
    if not include_ask_user:
        names = [
            name
            for name in names
            if name != ASK_USER_TOOL_NAME
            and name
            not in (
                "submit_task_graph",
                "get_task_graph",
                "update_task_graph",
            )
        ]
    tools = [FunctionTool(tool_impl(name)) for name in names]
    # 知识检索常驻装配，带助理的知识范围
    tools.extend(build_knowledge_tools(knowledge_scope))
    return tools
