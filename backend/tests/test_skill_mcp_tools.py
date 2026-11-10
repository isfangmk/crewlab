"""技能声明 MCP 工具：目录出参、参数映射、运行时白名单合并。

这些用例只碰纯函数，不需要起后端：MCP 目录来自探测缓存（McpToolRef），
不连远程 Server。
"""

from __future__ import annotations

import json

from google.adk.skills import Frontmatter, Skill

from app.models_orm.mcp import McpServer
from app.services.agent_service import (
    ResolvedAgent,
    effective_allowed_tools,
    filter_mcp_servers_by_tools,
)
from app.services.mcp_service import (
    McpToolRef,
    build_searchable_mcp_toolsets,
    mcp_tool_wire_name,
    skill_params_from_schema,
)
from app.services.skills_service import SkillEntry, declared_tools_of_skills, skill_to_wire

_WIRE = mcp_tool_wire_name("ai-engine", "get_host_load")


def _skill(tools: list[str]) -> Skill:
    return Skill(
        frontmatter=Frontmatter(
            name="host-load",
            description="用 MCP 工具查主机负载",
            metadata={
                "risk": "safe",
                "category": "运维",
                "adk_additional_tools": tools,
            },
        ),
        instructions="# 步骤\n\n1. 调用查询工具\n2. 汇总结果",
    )


def _ref(*, available: bool = True) -> McpToolRef:
    return McpToolRef(
        server_name="ai-engine",
        tool_name="get_host_load",
        wire_name=_WIRE,
        description="查询主机负载",
        input_schema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "主机名"},
                "minutes": {"type": "integer", "default": 5},
                "verbose": {"type": ["boolean", "null"]},
            },
            "required": ["host"],
        },
        available=available,
    )


def test_wire_exposes_mcp_tool_with_schema_params() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={_WIRE: _ref()})

    assert out["tools"] == [_WIRE]
    # Server 可用 -> 技能可装配，不再被当成「目录里没有的工具」
    assert out["inventory_only"] is False
    assert out["tool_refs"] == [
        {
            "name": _WIRE,
            "kind": "mcp",
            "server": "ai-engine",
            "available": True,
            "description": "查询主机负载",
        }
    ]
    # 参数来自 input_schema，类型映射与内置工具目录一致
    assert out["params"] == [
        {"name": "host", "type": "string", "required": True, "desc": "主机名"},
        {"name": "minutes", "type": "int", "required": False, "default": 5},
        {"name": "verbose", "type": "bool", "required": False},
    ]


def test_disabled_server_marks_skill_inventory_only() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={_WIRE: _ref(available=False)})

    assert out["inventory_only"] is True
    assert out["tool_refs"][0]["kind"] == "mcp"
    assert out["tool_refs"][0]["available"] is False


def test_unknown_mcp_tool_is_flagged() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={})

    assert out["inventory_only"] is True
    assert out["tool_refs"] == [
        {"name": _WIRE, "kind": "unknown", "available": False, "description": ""}
    ]
    assert out["params"] == []


def test_schema_params_handle_empty_schema() -> None:
    assert skill_params_from_schema({"type": "object"}) == []


def test_declared_tools_of_skills_dedupes() -> None:
    skills = [_skill([_WIRE]), _skill([_WIRE, "query_knowledge"])]
    assert declared_tools_of_skills(skills) == {_WIRE, "query_knowledge"}


def _agent(tools: list[str]) -> ResolvedAgent:
    return ResolvedAgent(
        name="ops",
        description="运维助理",
        when_to_use="查负载",
        system_prompt="",
        critical_reminder="",
        tools=tools,
        disallowed_tools=[],
        permission_mode="",
        model="",
        max_turns=0,
        source="user",
    )


def test_effective_allowed_tools_merges_skill_declared_mcp() -> None:
    allowed = effective_allowed_tools(_agent(["query_knowledge"]), [_skill([_WIRE])])
    # 技能后加声明也要补齐：否则 Server 不会被挂载
    assert allowed == ["query_knowledge", _WIRE]


def test_effective_allowed_tools_keeps_inherit_all() -> None:
    # 空白名单 = 继承全部，不展开成显式列表
    assert effective_allowed_tools(_agent([]), [_skill([_WIRE])]) == []


def _server(
    *,
    name: str = "ai-engine",
    tools: list[str] | None = None,
    status: str = "ok",
) -> McpServer:
    names = ["get_host_load", "list_hosts"] if tools is None else tools
    return McpServer(
        name=name,
        transport="http",
        endpoint="http://127.0.0.1:9999/mcp",
        credential="",
        header_template_json="{}",
        trusted=False,
        approval_policy="never",
        enabled=True,
        status=status,
        last_error="",
        tools_cache_json=json.dumps(
            [
                {
                    "name": item,
                    "description": f"{item} 工具",
                    "input_schema": {
                        "type": "object",
                        "properties": {"host": {"type": "string"}},
                    },
                }
                for item in names
            ],
            ensure_ascii=False,
        ),
    )


async def test_mcp_tools_enter_search_index_not_context() -> None:
    """MCP 工具一律不进初始上下文：只暴露 search/load，业务工具留在检索目录里。"""

    from app.services import tool_search_index

    server = _server()
    list_hosts = mcp_tool_wire_name("ai-engine", "list_hosts")
    # 检索目录构建只读进程内向量缓存（线上由 warm_vector_cache_from_db 预热）
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    tool_search_index._VECTOR_CACHE[list_hosts] = [0.0, 1.0]  # noqa: SLF001
    toolsets = await build_searchable_mcp_toolsets(None, [server])
    assert len(toolsets) == 1
    toolset = toolsets[0]
    try:
        # 上下文里只有 search/load 等管理工具，没有业务工具
        names = {tool.name for tool in await toolset.get_tools()}
        assert _WIRE not in names
        assert list_hosts not in names
        assert any(name.endswith("search_tools") for name in names)
        assert any(name.endswith("load_tool") for name in names)
        # 两台工具都在检索目录里，技能声明的也不做免检索直塞
        deferred = {tool.name for tool in await toolset._source.get_tools()}  # noqa: SLF001
        assert deferred == {_WIRE, list_hosts}
    finally:
        await toolset.close()
        tool_search_index._VECTOR_CACHE.pop(_WIRE, None)  # noqa: SLF001
        tool_search_index._VECTOR_CACHE.pop(list_hosts, None)  # noqa: SLF001


async def test_unmounted_mcp_server_is_not_searchable() -> None:
    """未挂载（白名单不含该 Server）或探测失败的 Server 不装配 toolset，自然也搜不到。"""

    server = _server()
    unmounted = filter_mcp_servers_by_tools([server], ["query_knowledge"])
    assert unmounted == []
    assert await build_searchable_mcp_toolsets(None, unmounted) == []

    broken = _server(status="error")
    assert await build_searchable_mcp_toolsets(None, [broken]) == []


async def test_unselected_tool_is_not_searchable() -> None:
    """白名单只勾了部分工具：没勾选的工具不能出现在检索目录里。"""

    from app.services import tool_search_index

    server = _server()
    list_hosts = mcp_tool_wire_name("ai-engine", "list_hosts")
    # 向量缓存里同时有两条：如果白名单没生效，list_hosts 会出现在索引里
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    tool_search_index._VECTOR_CACHE[list_hosts] = [0.0, 1.0]  # noqa: SLF001
    toolsets = await build_searchable_mcp_toolsets(
        None,
        [server],
        allowed_tools=["query_knowledge", _WIRE],
    )
    assert len(toolsets) == 1
    toolset = toolsets[0]
    try:
        # 检索目录里只剩白名单勾选的那条，未勾选的工具搜不到
        deferred = {tool.name for tool in await toolset._source.get_tools()}  # noqa: SLF001
        assert deferred == {_WIRE}
        # 勾选的工具同样不进上下文，仍要靠 search/load 获取
        names = {tool.name for tool in await toolset.get_tools()}
        assert _WIRE not in names
        assert any(name.endswith("search_tools") for name in names)
    finally:
        await toolset.close()
        tool_search_index._VECTOR_CACHE.pop(_WIRE, None)  # noqa: SLF001
        tool_search_index._VECTOR_CACHE.pop(list_hosts, None)  # noqa: SLF001


async def test_single_tool_server_still_keeps_search_entry() -> None:
    """整台 Server 只有一个工具也要保留 search/load，不能再退化成直接装配。"""

    from app.services import tool_search_index

    server = _server(tools=["get_host_load"])
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    toolsets = await build_searchable_mcp_toolsets(None, [server], allowed_tools=[_WIRE])
    assert len(toolsets) == 1
    toolset = toolsets[0]
    try:
        names = {tool.name for tool in await toolset.get_tools()}
        assert _WIRE not in names
        assert any(name.endswith("search_tools") for name in names)
    finally:
        await toolset.close()
        tool_search_index._VECTOR_CACHE.pop(_WIRE, None)  # noqa: SLF001
