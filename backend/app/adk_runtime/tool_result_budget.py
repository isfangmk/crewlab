"""工具结果大小治理：在 ADK after_tool_callback 统一收敛回喂模型的工具结果。

背景：MCP 工具（如 codebase-memory.get_architecture）可能返回数百万字符的结果，
整块作为 FunctionResponse 回喂模型会撑爆上下文（400 context length exceeded），
直接打断对话。这里用 ADK 官方的 after_tool_callback 作为所有工具、所有执行路径
（主对话 / 审批续跑 / 试跑台 / taskgraph / workflow / 子助理）的唯一收口，
在结果变成 FunctionResponse 之前做长度收敛，带截断标记让用户知情。

设计：只做逐字符串上限 max_chars——超大工具结果的根因是个别巨型字段
（如代码库投影的 220 万字符正文），把它们收敛到上限即可把结果从数百万字符
降到可控范围。短字段（路径/元数据/结构）原样保留，不破坏结果可用性。
- 递归遍历所有字符串叶子，超 max_chars 则截断并追加标记；
- 保留 dict/list 结构与标量字段，结果仍是合法 JSON；
- 生效值：助理级覆盖 > 全局默认 > 内置默认（在 resolve_max_tool_result_chars 统一解析）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# 内置兜底：10 万字符 ≈ 最坏情况（CJK 1 字符/token）10 万 token，远低于主流模型上下文上限。
DEFAULT_MAX_TOOL_RESULT_CHARS = 100_000
_TRUNCATION_MARKER = "...[truncated by tool result budget]"


def dedupe_mcp_result(tool_response: Any) -> Any:
    """MCP CallToolResult 去重：structuredContent 已是 content[].text 的结构化版本，
    两者内容重复会双倍占用 token。若存在字典型 structuredContent，则只保留它的字段，
    丢掉冗长的 content；isError 仅用于 ADK 错误遥测（几个字符），错误时保留。
    非 MCP 结构原样返回。
    """
    if not isinstance(tool_response, dict):
        return tool_response
    structured = tool_response.get("structuredContent")
    if not isinstance(structured, dict) or "content" not in tool_response:
        return tool_response
    result = dict(structured)
    if "isError" not in result and tool_response.get("isError"):
        result["isError"] = True
    return result


def resolve_max_tool_result_chars(
    agent_override: int | None, global_default: int | None
) -> int:
    """按 助理覆盖 > 全局默认 > 内置默认 解析有效上限；非正数视为未设置。"""
    for candidate in (agent_override, global_default):
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return DEFAULT_MAX_TOOL_RESULT_CHARS


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    keep = max(0, limit - len(_TRUNCATION_MARKER))
    tail = _TRUNCATION_MARKER[: max(0, limit - keep)]
    return text[:keep] + tail


def _iter_strings(value: Any) -> Iterator[str]:
    """按确定顺序递归遍历结构里所有字符串叶子。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def cap_tool_result(tool_response: Any, max_chars: int) -> tuple[Any, int, int]:
    """递归把每个字符串叶子收敛到单项上限 max_chars，保留结构；返回 (结果, 原始字符, 收敛后字符)。

    只截断超限的巨型字符串（附带标记），短字符串原样保留——根因是个别
    数百万字符的巨型字段，收敛它们即可把结果降到可控范围，同时不破坏
    结果里大量有价值的短元数据。供单测与 after_tool_callback 共用。
    """
    if max_chars <= 0 or not isinstance(tool_response, (dict, list)):
        return tool_response, 0, 0

    def cap(value: Any) -> Any:
        if isinstance(value, str):
            return _truncate(value, max_chars)
        if isinstance(value, dict):
            return {key: cap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cap(item) for item in value]
        return value

    original = sum(len(s) for s in _iter_strings(tool_response))
    capped = cap(tool_response)
    return capped, original, sum(len(s) for s in _iter_strings(capped))


def build_after_tool_callback(
    max_chars: int | None = None,
) -> Callable[..., Any]:
    """构造 ADK after_tool_callback：返回替换后的结果，或 None 表示不改。

    签名遵循 ADK AfterToolCallback：
        (tool, args, tool_context, tool_response) -> dict | None
    返回 dict 会替换原工具结果（_caller.py:642-645），正是回喂模型前的收口点。
    """
    limit = max_chars if (isinstance(max_chars, int) and max_chars > 0) else DEFAULT_MAX_TOOL_RESULT_CHARS

    async def after_tool_callback(
        tool: Any,
        args: dict[str, Any],  # pyright: ignore[reportUnusedParameter] — ADK 契约参数
        tool_context: Any,
        tool_response: dict[str, Any],
    ) -> dict[str, Any] | None:
        # 先去重 MCP 的 content/structuredContent 重复载荷，再做字符串截断。
        deduped = dedupe_mcp_result(tool_response)
        capped, original, final = cap_tool_result(deduped, limit)
        if final < original or deduped is not tool_response:
            session_id = ""
            try:
                session_id = tool_context.invocation_context.session.id
            except AttributeError:
                pass
            logger.warning(
                "工具结果已治理（去重/截断）tool=%s session_id=%s 原始字符=%s 收敛后字符=%s 上限=%s",
                getattr(tool, "name", "?"),
                session_id,
                original,
                final,
                limit,
            )
        return capped

    return after_tool_callback
