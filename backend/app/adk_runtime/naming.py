"""ADK 名称：Agent 名与工具展示名的解析。

ADK 要求 Agent 名是合法 Python 标识符，`user` 是保留名；业务名允许 `-`、
可以数字开头，不能直接使用。工具展示名用于日志列举，便于排查时看清挂了什么。
"""

from __future__ import annotations

import re
from typing import Any

from google.adk.tools.base_toolset import BaseToolset

# ADK 内部保留给终端用户消息的 author 名
_ADK_RESERVED = {"user"}


def adk_identifier(name: str) -> str:
    """业务名 → 合法 Python 标识符。"""

    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if cleaned == "" or cleaned[0].isdigit():
        cleaned = f"agent_{cleaned}"
    if cleaned in _ADK_RESERVED:
        cleaned = f"agent_{cleaned}"
    return cleaned


def root_adk_name(agent_name: str) -> str:
    """根 Agent 的 ADK 名：默认助理沿用历史名，其余按业务名派生。

    历史会话里 assistant 事件的 author 是 workbench_chat，默认助理保持不变；
    自定义根助理用 `agent_<业务名>`，避免与净化后的名字撞车。
    """

    if agent_name == "default":
        return "workbench_chat"
    return f"agent_{adk_identifier(agent_name)}"


def tool_display_name(tool: Any) -> str:
    """工具展示名：优先 wire/name 属性，无名字时退到类名，供日志列举工具清单。"""

    name = getattr(tool, "name", None)
    if isinstance(name, str) and name != "":
        return name
    return type(tool).__name__


def toolset_display_name(toolset: Any) -> str:
    """工具集展示名：带出具体标识，避免日志里只有一个笼统的类名。

    SkillToolset 列出其技能 key；MCP 可搜索/直挂工具集用 namespace
    （形如 mcp__<server>）标识是哪个服务；都没有才退到类名。
    """

    skills = getattr(toolset, "skills", None)
    if isinstance(skills, (list, tuple)) and skills:
        names = [
            skill_name
            for s in skills
            if isinstance((skill_name := getattr(s, "name", None)), str) and skill_name != ""
        ]
        if names:
            return f"{type(toolset).__name__}(skills={sorted(names)})"
    namespace = getattr(toolset, "namespace", None)
    if isinstance(namespace, str) and namespace != "":
        return f"{type(toolset).__name__}({namespace})"
    return type(toolset).__name__


def split_tools_and_toolsets(items: list[Any]) -> tuple[list[str], list[str]]:
    """把装配项分成单个工具与工具集两组展示名。

    SkillToolset / SearchableToolset 是 BaseToolset（按目录提供工具），
    不是单个工具：混在 tools 里会让人误读成挂了一个叫 SkillToolset 的工具。
    """

    tools: list[str] = []
    toolsets: list[str] = []
    for item in items:
        if isinstance(item, BaseToolset):
            toolsets.append(toolset_display_name(item))
        else:
            tools.append(tool_display_name(item))
    return tools, toolsets
