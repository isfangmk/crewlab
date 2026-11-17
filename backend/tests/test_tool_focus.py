"""工具 focus 收窄：对可搜索 MCP toolset 的保留/过滤语义。

真依赖：用真实 SearchableToolset 对象验证过滤逻辑，不打桩。
MCP 走 discovery 入口 + search→load 延迟加载，focus 里的业务工具名（mcp__*）
不能按挂载名匹配，而要按命名空间保留 toolset。
"""

from __future__ import annotations

from adk_tool_search.toolset import SearchableToolset
from google.adk.tools.base_toolset import BaseToolset

from app.services.execution.tool_policy import ToolFocus, filter_tools_by_focus


def _toolset(namespace: str) -> BaseToolset:
    return SearchableToolset(namespace=namespace)


def test_focus_keeps_mcp_toolset_by_namespace() -> None:
    """focus 声明了某 server 的业务工具 → 保留该 server 的可搜索 toolset。"""

    toolset = _toolset("mcp__obs-tools")
    kept = filter_tools_by_focus([toolset], {"mcp__obs-tools__list_alerts"})
    assert kept == [toolset]


def test_focus_drops_mcp_toolset_without_declared_tool() -> None:
    """focus 未声明该 server 的任何工具 → 不挂该 toolset。"""

    toolset = _toolset("mcp__obs-tools")
    assert filter_tools_by_focus([toolset], {"recall_memory"}) == []
    assert filter_tools_by_focus([toolset], {"mcp__add-work__create_issue"}) == []


def test_focus_none_drops_everything() -> None:
    """__none__ 清空全部（含 MCP toolset）。"""

    toolset = _toolset("mcp__obs-tools")
    assert filter_tools_by_focus([toolset], ToolFocus.NONE) == []


def test_focus_empty_keeps_all() -> None:
    """未声明（空 focus）保持全量。"""

    toolset = _toolset("mcp__obs-tools")
    assert filter_tools_by_focus([toolset], None) == [toolset]
