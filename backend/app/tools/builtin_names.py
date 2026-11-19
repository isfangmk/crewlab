"""可勾选内置工具名单（单一数据源）。

独立成无依赖模块，供助理配置（agent_service）、工具装配（catalog/toolset）共用，
避免 agent_service 顶层 import catalog 触发
catalog → memory → memory_service → agent_service 的循环导入。
"""

from __future__ import annotations

# 可在助理页勾选的内置工具；web_search 由对话联网开关控制，不在其中。
# 顺序即展示与默认值顺序。
BUILTIN_SELECTABLE_TOOL_NAMES: tuple[str, ...] = (
    "run_python",
    "remember_memory",
    "recall_memory",
    "update_memory",
    "delete_memory",
    "query_knowledge",
    "ask_user",
    "submit_task_graph",
    "get_task_graph",
    "update_task_graph",
    "create_skill",
    "install_skill_from_url",
    "install_skill_from_zip",
    "search_platform_api",
    "call_platform_api",
)


def all_builtin_tool_names() -> list[str]:
    """全部可勾选的内置工具名；新助理默认全选。"""

    return list(BUILTIN_SELECTABLE_TOOL_NAMES)
