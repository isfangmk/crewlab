"""无人值守 agent 的工具策略：只读闸门与挂载面收窄。

任务图节点复用主助理装配，但内置工具只挂只读白名单；外部 MCP / 技能工具
按名称启发式禁写。闸门只返回错误结果、绝不进审批确认——无人值守不能悬挂。
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Awaitable, Callable

from google.adk.tools import FunctionTool
from google.adk.tools.base_toolset import BaseToolset

from app.tools.catalog import tool_impl

logger = logging.getLogger(__name__)

# 只读内置工具白名单：只查不改。
# query_knowledge 不在此列：知识检索由 build_knowledge_tools 按知识范围挂载，
# 两处同名注册会让模型侧校验「Tool names must be unique」直接拒单。
READONLY_TOOL_NAMES: tuple[str, ...] = (
    "recall_memory",
    "list_workflows",
    "get_workflow",
    "list_workflow_runs",
    "get_workflow_run",
)

# 允许 run_python 的扩展只读集：汇总分析/画图属分析产出，不是写操作。
ANALYST_TOOL_NAMES: tuple[str, ...] = READONLY_TOOL_NAMES + ("run_python",)

# 外部工具名禁写启发式：前缀动词或尾段动词即拒。
# 只按命名判断（MCP 无只读标记），已知有漏网风险，写操作只应出现在主代理层。
_WRITE_NAME_RE = re.compile(
    r"(?:^|__)(?:create|update|delete|remove|insert|drop|send|write|apply|execute"
    r"|push|restart|kill|cancel|edit|modify|upload|trigger|start|stop|run|invoke"
    r"|set|put|patch|post|add)(?:_|$)"
    r"|_(?:create|update|delete|remove|add|apply|send|write)$",
    re.IGNORECASE,
)

# 统筹纪律：所有无人值守节点共用，装配点统一注入。
# 工具面已按任务裁剪，但挂载不代表都要用——不加纪律时目标松散的单元会乱逛无关工具。
FOCUS_GUIDANCE = (
    "聚焦纪律：只调用与本任务直接相关的工具，已挂载的工具不代表都要用一遍；"
    "与本任务无关的工具一律不调用，禁止为了「看看有没有」而扫描探索；"
    "证据不足时在结论里标注缺口，不要为了凑证据扩大调用范围。"
    "收尾纪律：一旦已有足够信息回答本任务就立即停止检索、直接给结论，"
    "不要重复调用同一工具或将同类检索再试一遍；够用即止，避免无谓的再次调用。"
    "证据复用：多条结论可共享同一次检索的结果，引用其中的 call_id 即可，"
    "不要为了让每条结论各带一个调用记录而反复检索同一主题。"
)


class ToolFocus(str, Enum):
    """工具挂载面的显式表达：NONE 表示不挂任何工具（区别于未声明的全量）。"""

    NONE = "__none__"


def normalize_tool_focus(
    focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | None,
) -> ToolFocus | frozenset[str] | None:
    """把上层声明归一成挂载口径：含 NONE 哨兵 → NONE；空 → None（全量）。"""

    if focus is ToolFocus.NONE:
        return ToolFocus.NONE
    if not focus:
        return None
    if ToolFocus.NONE.value in focus:
        return ToolFocus.NONE
    return frozenset(str(name) for name in focus)


def filter_tools_by_focus(
    tools: list[Any],
    focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | frozenset[str] | None,
) -> list[Any]:
    """按声明收窄挂载面；NONE 不挂任何工具，空/None 保持全量。

    知识检索闭包版的函数名是 scoped_query_knowledge，按 query_knowledge 匹配。

    MCP 走可搜索 toolset：初始挂载的是 discovery 入口，业务工具名（mcp__*）
    要经 search→load 才进入上下文。因此 toolset 不能按 discovery 工具名匹配，
    而按 focus 是否声明了该命名空间（mcp__<server>__）下的业务工具来判断保留。
    """

    if focus is ToolFocus.NONE:
        return []
    if not focus:
        return tools
    wanted = set(focus)
    kept: list[Any] = []
    for tool in tools:
        # 可搜索 MCP toolset：按命名空间声明保留，而非按 discovery 入口名匹配。
        if isinstance(tool, BaseToolset):
            ns = getattr(tool, "namespace", "") or ""
            if isinstance(ns, str) and ns and any(
                name.startswith(f"{ns}__") for name in wanted
            ):
                kept.append(tool)
            continue
        name = getattr(tool, "name", "") or ""
        if name == "scoped_query_knowledge":
            name = "query_knowledge"
        if name in wanted:
            kept.append(tool)
    return kept


def build_readonly_tool_gate(
    disallowed_tool_names: set[str] | None = None,
    allow: set[str] | None = None,
) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    """只读闸门：命中禁写启发式或禁用名单即拒绝，永不进审批确认。

    allow 是免启发式白名单（如分析节点的 run_python）：内置只读白名单是权威，
    启发式只拦 MCP / 技能这类没有只读标记的外部工具。
    """

    denied = set(disallowed_tool_names or ())
    permitted = set(allow or ())

    # args / tool_context 是 ADK before_tool_callback 契约参数（SDK 调用）。
    # noqa 与 pyright 忽略都要压在签名行本身（按行生效）。
    async def before_tool_callback(tool: Any, args: Any, tool_context: Any) -> dict[str, Any] | None:  # pyright: ignore[reportUnusedParameter]
        name = getattr(tool, "name", "") or ""
        if name in permitted:
            return None
        if name in denied or _WRITE_NAME_RE.search(name):
            logger.info("只读闸门拒绝工具 name=%s", name)
            return {"error": f"无人值守执行：工具 {name} 被拒绝，仅允许只读查询"}
        return None

    return before_tool_callback


def build_readonly_builtin_tools(names: tuple[str, ...]) -> list[FunctionTool]:
    """按只读白名单构建内置 FunctionTool 列表。"""

    return [FunctionTool(tool_impl(name)) for name in names]
