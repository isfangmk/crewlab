"""对话开关打开时挂载的 web_search Tool（DeepSeek 官方搜索）。"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from app.services.web_search_service import deepseek_web_search


def build_web_search_tool(*, base_url: str, api_key: str) -> FunctionTool:
    """用当前 deepseek 凭证构造一轮可用的 web_search。"""

    async def web_search(query: str, count: int = 5) -> dict:
        """使用 DeepSeek 官方网页搜索检索公开互联网。"""

        return await deepseek_web_search(
            base_url=base_url,
            api_key=api_key,
            query=query,
            count=count,
        )

    return FunctionTool(web_search)
