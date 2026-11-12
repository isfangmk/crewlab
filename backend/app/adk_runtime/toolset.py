"""把内置工具装配成 ADK 可挂载的 FunctionTool 列表。

只做装配：工具行为在 app/tools 各主题模块，目录与注册表在 app/tools/catalog。
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from app.services.knowledge_scope import KnowledgeScope
from app.tools.catalog import builtin_tool_registry, tool_impl, tool_param_specs, workflow_tool_names
from app.tools.knowledge import query_knowledge, scoped_query_knowledge

logger = logging.getLogger(__name__)

__all__ = [
    "build_knowledge_tools",
    "build_builtin_tools",
    "builtin_tool_registry",
    "tool_param_specs",
]


def build_knowledge_tools(scope: KnowledgeScope | None = None) -> list[FunctionTool]:
    """供 SkillToolset.additional_tools 挂载；scope 为空时使用全库检索。

    顺带挂上工作流工具组：它们同样不依赖外部凭证，回答「上次那个工作流
    跑到哪了、哪一步失败」这类问题时必须能查画布与运行记录。
    """

    workflow_tools = [FunctionTool(tool_impl(name)) for name in workflow_tool_names()]
    if scope is None or scope.is_empty:
        return [FunctionTool(query_knowledge), *workflow_tools]
    logger.info(
        "装配带知识范围的 query_knowledge paths=%s doc_ids=%s",
        list(scope.paths),
        len(scope.doc_ids),
    )
    return [FunctionTool(scoped_query_knowledge(scope)), *workflow_tools]


def build_builtin_tools() -> list[FunctionTool]:
    """初始上下文不挂 Tool；由 Skill 激活后注入。"""

    return []
