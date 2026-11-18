"""工具结果大小治理：after_tool_callback 统一截断 + 有效值解析优先级。

纯逻辑单测：不依赖外部服务，直接验证收敛结果与解析优先级。
"""

from __future__ import annotations

import json

from app.adk_runtime.tool_result_budget import (
    _TRUNCATION_MARKER,
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    build_after_tool_callback,
    cap_tool_result,
    dedupe_mcp_result,
    resolve_max_tool_result_chars,
)


class _FakeTool:
    name = "mcp__codebase-memory__get_architecture"


class _FakeCtx:
    class invocation_context:
        class session:
            id = "s-test"


def test_resolve_precedence() -> None:
    """助理覆盖 > 全局默认 > 内置默认；非正数/None 视为未设置。"""
    assert resolve_max_tool_result_chars(50000, 80000) == 50000
    assert resolve_max_tool_result_chars(None, 80000) == 80000
    assert resolve_max_tool_result_chars(0, 80000) == 80000
    assert resolve_max_tool_result_chars(None, None) == DEFAULT_MAX_TOOL_RESULT_CHARS
    assert resolve_max_tool_result_chars(-1, 0) == DEFAULT_MAX_TOOL_RESULT_CHARS


def test_cap_tool_result_truncates_strings_keeps_structure() -> None:
    """超长字符串被截断并带标记；dict/list 结构与标量字段不变；结果仍是合法 JSON。"""
    huge = "x" * 4_800_000
    response = {
        "content": [{"type": "text", "text": huge}],
        "structuredContent": {"result": huge, "status": "ok", "count": 3},
        "isError": False,
    }
    capped, original, final = cap_tool_result(response, 100_000)

    # 原始字符数约两个 huge，收敛后远小于上限总和
    assert original >= 9_600_000
    assert final < original
    # 每个巨型字符串都被收敛到单项上限（约 2 个 x 100_000），短字段原样保留。
    assert final <= 100_000 * 2 + 1000

    # 结构完整
    assert isinstance(capped, dict)
    assert capped["isError"] is False
    assert capped["structuredContent"]["status"] == "ok"
    assert capped["structuredContent"]["count"] == 3
    assert isinstance(capped["content"], list) and len(capped["content"]) == 1

    # 长字符串已带截断标记且长度受控
    capped_text = capped["content"][0]["text"]
    assert capped_text.endswith(_TRUNCATION_MARKER)
    assert len(capped_text) <= 100_000
    assert len(capped["structuredContent"]["result"]) <= 100_000

    # JSON 合法（回喂模型的 FunctionResponse 必须是合法 JSON）
    assert isinstance(json.dumps(capped, ensure_ascii=False), str)


def test_cap_tool_result_noop_when_small() -> None:
    """未超限的结果原样透出，字符数不变。"""
    response = {"content": [{"type": "text", "text": "short"}]}
    capped, original, final = cap_tool_result(response, 100_000)
    assert final == original
    assert capped == response


def test_dedupe_mcp_result_drops_content_when_structured() -> None:
    """有 structuredContent 时只保留它（避免 content/structuredContent 双倍占 token）；
    保留 isError 供错误遥测；非 MCP 结构原样返回。"""
    mcp = {
        "content": [{"type": "text", "text": '{"error":"function not found"}'}],
        "structuredContent": {"error": "function not found", "function_name": "foo"},
        "isError": True,
    }
    out = dedupe_mcp_result(mcp)
    assert out == {
        "error": "function not found",
        "function_name": "foo",
        "isError": True,
    }
    assert "content" not in out

    # 无 structuredContent：原样
    plain = {"content": [{"type": "text", "text": "hi"}]}
    assert dedupe_mcp_result(plain) is plain


async def test_after_tool_callback_replaces_oversized_result() -> None:
    """after_tool_callback 对超限结果返回替换后的 dict，未超限返回同值。"""
    cb = build_after_tool_callback(1000)
    huge = "y" * 5000
    result = await cb(_FakeTool(), {}, _FakeCtx(), {"content": [{"text": huge}]})
    assert result is not None
    assert result["content"][0]["text"].endswith(_TRUNCATION_MARKER)
    assert len(result["content"][0]["text"]) <= 1000

    small = await cb(_FakeTool(), {}, _FakeCtx(), {"content": [{"text": "ok"}]})
    assert small == {"content": [{"text": "ok"}]}
