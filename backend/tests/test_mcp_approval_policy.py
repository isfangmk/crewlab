"""审批策略：never 不进名单；always 全进；non_readonly 只进非只读。"""

from __future__ import annotations

import pytest

from app.models_orm.mcp import McpServer
from app.services.mcp_service import confirm_tool_names_for_servers, effective_approval_policy


def _server(
    *,
    name: str,
    policy: str,
    tools: list[dict],
) -> McpServer:
    import json

    row = McpServer(
        id=1,
        name=name,
        transport="http",
        endpoint="http://127.0.0.1:9",
        credential="",
        header_template_json="{}",
        approval_policy=policy,
        enabled=True,
        status="ok",
        last_error="",
        tools_cache_json=json.dumps(tools, ensure_ascii=False),
    )
    return row


def test_effective_policy_validates_values() -> None:
    assert effective_approval_policy(_server(name="demo", policy="always", tools=[])) == "always"
    with pytest.raises(ValueError):
        effective_approval_policy(_server(name="demo2", policy="sideways", tools=[]))


def test_confirm_names_always_vs_never() -> None:
    tools = [
        {"name": "list_items", "description": "list"},
        {"name": "delete_item", "description": "mutate"},
    ]
    always = _server(name="svc", policy="always", tools=tools)
    never = _server(name="svc2", policy="never", tools=tools)
    names_always = confirm_tool_names_for_servers([always])
    names_never = confirm_tool_names_for_servers([never])
    assert any(n.endswith("__delete_item") for n in names_always)
    assert names_never == set()


def test_non_readonly_skips_list_prefix() -> None:
    tools = [
        {"name": "list_items", "description": "list all"},
        {"name": "delete_item", "description": "delete"},
    ]
    s = _server(name="svc", policy="non_readonly", tools=tools)
    names = confirm_tool_names_for_servers([s])
    assert not any(n.endswith("__list_items") for n in names)
    assert any(n.endswith("__delete_item") for n in names)
