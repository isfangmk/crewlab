"""工具审批闸门：before_tool_callback + ADK request_confirmation。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# 与 ADK FunctionTool.require_confirmation 暂停文案一致，供 SSE 识别
_PENDING_CONFIRMATION_ERROR = (
    "This tool call requires confirmation, please approve or reject."
)
_REJECTED_ERROR = "This tool call is rejected."
# 助理禁用名单命中：模型看到的是明确拒绝，不会误以为工具不存在
_DISALLOWED_ERROR = "This tool is disabled for the current assistant."


def build_before_tool_callback(
    confirm_tool_names: set[str],
    disallowed_tool_names: set[str] | None = None,
) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    """按需确认的工具名集合构造 before_tool_callback。

    disallowed_tool_names 是助理级禁用名单：命中的工具直接拒绝执行，
    不进入审批流程（禁用是硬约束，审批是运行时确认）。
    """

    for name in confirm_tool_names:
        if not isinstance(name, str) or name == "":
            raise ValueError("confirm_tool_names 含无效工具名")
    blocked = set() if disallowed_tool_names is None else set(disallowed_tool_names)
    for name in blocked:
        if not isinstance(name, str) or name == "":
            raise ValueError("disallowed_tool_names 含无效工具名")

    async def before_tool_callback(
        tool: BaseTool,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        tool_name = tool.name
        if tool_name in blocked:
            logger.info("工具被助理禁用名单拦截 tool=%s", tool_name)
            return {"error": _DISALLOWED_ERROR}
        if tool_name not in confirm_tool_names:
            return None

        confirmation = tool_context.tool_confirmation
        if confirmation is None:
            logger.info(
                "工具调用需审批 tool=%s arg_keys=%s",
                tool_name,
                sorted(args.keys()) if isinstance(args, dict) else [],
            )
            tool_context.request_confirmation(
                hint=(
                    f"Please approve or reject the tool call {tool_name}() by"
                    " responding with a FunctionResponse with an expected"
                    " ToolConfirmation payload."
                ),
            )
            tool_context.actions.skip_summarization = True
            return {"error": _PENDING_CONFIRMATION_ERROR}
        if not confirmation.confirmed:
            logger.info("工具调用被拒绝 tool=%s", tool_name)
            return {"error": _REJECTED_ERROR}
        logger.info("工具调用已批准，放行 tool=%s", tool_name)
        return None

    return before_tool_callback
