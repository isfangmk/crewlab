"""平台助手元工具 + 澄清工具的 ADK 装配器。

只在「平台助手」这个助理里挂载（由 build_agent_tool_bundle 按助理名判断），
不进通用 builtin 清单——避免污染其它助理的可见工具。
FunctionTool 包装的 async 函数里 tool_context 由 ADK 自动注入，不进模型可见 schema。
"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from app.tools.platform_api import call_platform_api, search_platform_api
from app.tools.user_input import ask_user

# 需要人工审批的工具名：call_platform_api 写操作进闸，GET 由审批回调动态放行。
# ask_user 有独立的澄清闸门（tool_approval 特判），不进此名单。
PLATFORM_CONFIRM_TOOL = "call_platform_api"


def build_platform_tools() -> list[FunctionTool]:
    """构造平台助手的工具：两个元工具 + 澄清提问。

    平台助手参数不足时要用 ask_user 向用户澄清（不臆造标识/占位值），
    因此它和其它助理一样需要挂载 ask_user。
    """
    return [
        FunctionTool(search_platform_api),
        FunctionTool(call_platform_api),
        FunctionTool(ask_user),
    ]
