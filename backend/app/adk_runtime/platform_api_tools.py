"""平台助手两个元工具的 ADK 装配器。

只在「平台助手」这个助理里挂载（由 build_agent_tool_bundle 按助理名判断），
不进通用 builtin 清单——避免污染其它助理的可见工具。
FunctionTool 包装的 async 函数里 tool_context 由 ADK 自动注入，不进模型可见 schema。
"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from app.tools.platform_api import call_platform_api, search_platform_api

# 平台助手的业务助理名（与 UserAgent.name 对应，阶段二按此名创建）
PLATFORM_AGENT_NAME = "platform-assistant"

# 需要人工审批的工具名：call_platform_api 写操作进闸，GET 由审批回调动态放行
PLATFORM_CONFIRM_TOOL = "call_platform_api"


def build_platform_tools() -> list[FunctionTool]:
    """构造平台助手的两个元工具。"""
    return [
        FunctionTool(search_platform_api),
        FunctionTool(call_platform_api),
    ]
