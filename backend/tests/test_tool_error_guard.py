"""工具异常兜底：on_tool_error_callback 把报错转成错误结果回给模型。

ADK 对非 MCP 工具的异常默认不兜底，异常冒泡会终止整个 agent 回合
（对话轮次 / worker 一路）；这里钉住装配入口的统一兜底行为。
"""

from __future__ import annotations

import pytest

from app.adk_runtime.agent_factory import build_chat_agent, tool_error_as_result
from app.adk_runtime.web_search_tool import build_web_search_tool
from app.services.platform.web_search_service import deepseek_web_search


class _Boom:
    """工具替身：只提供 name，run 时抛错。"""

    name = "boom_tool"


@pytest.mark.asyncio
async def test_tool_error_becomes_result_with_reason_and_retry() -> None:
    """工具抛错 → 错误结果（带原因与重试指引），异常不冒泡。"""

    result = await tool_error_as_result(_Boom(), {}, None, ValueError("炸了"))
    assert "error" in result
    assert "boom_tool" in result["error"]
    assert "炸了" in result["error"]
    assert "重试" in result["error"]


def test_chat_agent_registers_tool_error_callback() -> None:
    """对话/子代理/worker 共用的装配入口必须挂上兜底回调。"""

    agent = build_chat_agent(
        provider_id="custom",
        model="m",
        model_prefix="openai/",
        base_url="https://example.com/v1",
        api_key="k",
        agent_name="t",
        agent_description="",
        persona_instruction="",
        thinking_enabled=False,
    )
    assert len(agent.canonical_on_tool_error_callbacks) == 1


def test_web_search_description_states_char_limit() -> None:
    """工具描述必须写明字符限制：模型看得见才知道要压缩关键词。"""

    tool = build_web_search_tool(base_url="https://example.com", api_key="k")
    assert "128" in tool.description
    assert "query" in tool.description


@pytest.mark.asyncio
async def test_web_search_long_query_reports_actionable_error() -> None:
    """超长 query 的报错要带实际长度与重试指引。"""

    with pytest.raises(ValueError) as excinfo:
        await deepseek_web_search(
            base_url="https://example.com",
            api_key="k",
            query="字" * 200,
        )
    message = str(excinfo.value)
    assert "200" in message
    assert "128" in message
    assert "重试" in message


@pytest.mark.asyncio
async def test_session_web_search_requires_toggle() -> None:
    """联网搜索只在对话开启 web_search 时挂载；未开启直接 None。"""

    import uuid as _uuid

    from app.db import SessionLocal
    from app.models.chat import ChatSession

    from app.adk_runtime.web_search_tool import build_session_web_search_tool

    session = ChatSession(
        id=f"websearch-{_uuid.uuid4().hex[:8]}", title="t", web_search_enabled=False
    )
    async with SessionLocal() as db:
        assert await build_session_web_search_tool(db, session) is None
