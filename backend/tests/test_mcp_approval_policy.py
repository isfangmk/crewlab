"""审批策略：trusted/never 不进名单；always 进名单。"""

from __future__ import annotations

from app.models_orm.mcp import McpServer
from app.services.mcp_service import confirm_tool_names_for_servers, effective_approval_policy


def _server(
    *,
    name: str,
    trusted: bool,
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
        trusted=trusted,
        approval_policy=policy,
        enabled=True,
        status="ok",
        last_error="",
        tools_cache_json=json.dumps(tools, ensure_ascii=False),
    )
    return row


def test_effective_policy_trusted_is_never() -> None:
    s = _server(
        name="demo",
        trusted=True,
        policy="always",
        tools=[{"name": "whoami", "description": "x"}],
    )
    assert effective_approval_policy(s) == "never"


def test_confirm_names_always_vs_never() -> None:
    tools = [
        {"name": "list_items", "description": "list"},
        {"name": "delete_item", "description": "mutate"},
    ]
    always = _server(name="svc", trusted=False, policy="always", tools=tools)
    never = _server(name="svc2", trusted=False, policy="never", tools=tools)
    names_always = confirm_tool_names_for_servers([always])
    names_never = confirm_tool_names_for_servers([never])
    assert any(n.endswith("__delete_item") for n in names_always)
    assert names_never == set()


def test_non_readonly_skips_list_prefix() -> None:
    tools = [
        {"name": "list_items", "description": "list all"},
        {"name": "delete_item", "description": "delete"},
    ]
    s = _server(name="svc", trusted=False, policy="non_readonly", tools=tools)
    names = confirm_tool_names_for_servers([s])
    assert not any(n.endswith("__list_items") for n in names)
    assert any(n.endswith("__delete_item") for n in names)
