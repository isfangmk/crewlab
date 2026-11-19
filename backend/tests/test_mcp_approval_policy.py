"""MCP 工具风险定级：只读启发式给工具打 safe/mutating，供审批规则按 risk 过滤。

审批策略（per-server）已删除：MCP 工具默认免审，是否审批统一由审批规则决定。
这里只验证风险标签本身——它与是否审批解耦，规则可用它做 risk 维度匹配。
"""

from __future__ import annotations

import json

from app.models.mcp import McpServer
from app.services.mcp.mcp_service import tool_risk_map


def _server(*, name: str, tools: list[dict]) -> McpServer:
    return McpServer(
        id=1,
        name=name,
        transport="http",
        endpoint="http://127.0.0.1:9",
        credential="",
        header_template_json="{}",
        enabled=True,
        status="ok",
        last_error="",
        tools_cache_json=json.dumps(tools, ensure_ascii=False),
    )


def test_tool_risk_map_labels_by_readonly_heuristic() -> None:
    tools = [
        {"name": "list_items", "description": "list all"},
        {"name": "delete_item", "description": "delete"},
    ]
    assert tool_risk_map(_server(name="svc", tools=tools)) == {
        "list_items": "safe",
        "delete_item": "mutating",
    }


def test_tool_risk_map_empty_cache() -> None:
    """未探测/待重探的 Server 缓存为空，返回空映射，展示层不报错。"""
    assert tool_risk_map(_server(name="empty", tools=[])) == {}


def test_tool_risk_map_readonly_prefix_and_description() -> None:
    """只读判定同时看 get_/list_/search_ 等前缀与描述里的 read-only 字样。"""
    tools = [
        {"name": "get_user", "description": "fetch"},
        {"name": "search_docs", "description": "full text search"},
        {"name": "custom_op", "description": "read-only operation"},
        {"name": "do_thing", "description": "writes data"},
    ]
    result = tool_risk_map(_server(name="mixed", tools=tools))
    assert result == {
        "get_user": "safe",
        "search_docs": "safe",
        "custom_op": "safe",
        "do_thing": "mutating",
    }
