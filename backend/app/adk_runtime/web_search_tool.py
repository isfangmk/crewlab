"""对话开关打开时挂载的 web_search Tool（DeepSeek 官方搜索）。"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from app.services.platform.web_search_service import (
    deepseek_web_search,
    resolve_deepseek_search_cred,
)

logger = logging.getLogger(__name__)


def build_web_search_tool(*, base_url: str, api_key: str) -> FunctionTool:
    """用当前 deepseek 凭证构造一轮可用的 web_search。"""

    async def web_search(query: str, count: int = 5) -> dict:
        """使用 DeepSeek 官方网页搜索检索公开互联网。

        Args:
            query: 检索关键词，最长 128 字符，超出会报错；请用简短关键词，不要整句。
            count: 返回条数 1..50，默认 5。
        """

        return await deepseek_web_search(
            base_url=base_url,
            api_key=api_key,
            query=query,
            count=count,
        )

    return FunctionTool(web_search)


async def build_session_web_search_tool(db, session) -> FunctionTool | None:
    """联网搜索工具：只在对话开启 web_search 且凭证可用时返回，否则 None。

    无人值守执行（排查 worker / 计划步骤）共用：开关关掉就整路不挂；
    凭证不可用不阻断执行，退回内网取证。
    """

    if not session.web_search_enabled:
        return None
    try:
        base_url, api_key = await resolve_deepseek_search_cred(db)
        return build_web_search_tool(base_url=base_url, api_key=api_key)
    except Exception:  # noqa: BLE001 — 凭证不可用不阻断执行，退回内网取证
        logger.exception("联网搜索凭证不可用，跳过挂载 session_id=%s", session.id)
        return None
