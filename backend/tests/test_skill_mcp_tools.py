"""技能工具 / MCP 检索：MCP 工具一律不进初始上下文，业务工具留在检索目录里。

这些用例只碰纯函数：MCP 目录来自探测缓存，不连远程 Server。
"""

from __future__ import annotations

import json

from app.db import SessionLocal
from app.models.mcp import McpServer
from app.services.agent.agent_service import filter_mcp_servers_by_tools
from app.services.mcp.mcp_service import (
    build_searchable_mcp_toolsets,
    mcp_tool_wire_name,
    skill_params_from_schema,
)

_WIRE = mcp_tool_wire_name("ai-engine", "get_host_load")


def test_schema_params_handle_empty_schema() -> None:
    assert skill_params_from_schema({"type": "object"}) == []


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


def test_chat_instruction_pins_tool_names() -> None:
    """主指令不许把 search/load 说成通用动作：那会诱导模型发明裸名 search_tools。"""

    from app.adk_runtime.agent_factory import CHAT_AGENT_INSTRUCTION

    assert "禁止发明清单外的工具名" in CHAT_AGENT_INSTRUCTION
    assert "mcp__<服务>__search_tools" in CHAT_AGENT_INSTRUCTION
    assert "search/load 类工具" not in CHAT_AGENT_INSTRUCTION


async def test_mcp_tools_enter_search_index_not_context() -> None:
    """MCP 工具一律不进初始上下文：只暴露 search/load，业务工具留在检索目录里。"""

    from app.services.mcp import tool_search_index

    server = _server()
    list_hosts = mcp_tool_wire_name("ai-engine", "list_hosts")
    # 检索目录构建只读进程内向量缓存（线上由 warm_vector_cache_from_db 预热）
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    tool_search_index._VECTOR_CACHE[list_hosts] = [0.0, 1.0]  # noqa: SLF001
    async with SessionLocal() as db:
        toolsets = await build_searchable_mcp_toolsets(db, [server])
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


async def test_empty_whitelist_mounts_no_mcp() -> None:
    """空白名单不挂任何 MCP：Server 与工具都不进检索目录。"""

    server = _server()
    async with SessionLocal() as db:
        assert await build_searchable_mcp_toolsets(db, [server], allowed_tools=[]) == []


async def test_unmounted_mcp_server_is_not_searchable() -> None:
    """未挂载（白名单不含该 Server）或探测失败的 Server 不装配 toolset，自然也搜不到。"""

    server = _server()
    unmounted = filter_mcp_servers_by_tools([server], ["query_knowledge"])
    assert unmounted == []
    async with SessionLocal() as db:
        assert await build_searchable_mcp_toolsets(db, unmounted) == []

        broken = _server(status="error")
        assert await build_searchable_mcp_toolsets(db, [broken]) == []


async def test_unselected_tool_is_not_searchable() -> None:
    """白名单只勾了部分工具：没勾选的工具不能出现在检索目录里。"""

    from app.services.mcp import tool_search_index

    server = _server()
    list_hosts = mcp_tool_wire_name("ai-engine", "list_hosts")
    # 向量缓存里同时有两条：如果白名单没生效，list_hosts 会出现在索引里
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    tool_search_index._VECTOR_CACHE[list_hosts] = [0.0, 1.0]  # noqa: SLF001
    async with SessionLocal() as db:
        toolsets = await build_searchable_mcp_toolsets(
            db,
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

    from app.services.mcp import tool_search_index

    server = _server(tools=["get_host_load"])
    tool_search_index._VECTOR_CACHE[_WIRE] = [0.0, 1.0]  # noqa: SLF001
    async with SessionLocal() as db:
        toolsets = await build_searchable_mcp_toolsets(db, [server], allowed_tools=[_WIRE])
    assert len(toolsets) == 1
    toolset = toolsets[0]
    try:
        names = {tool.name for tool in await toolset.get_tools()}
        assert _WIRE not in names
        assert any(name.endswith("search_tools") for name in names)
    finally:
        await toolset.close()
        tool_search_index._VECTOR_CACHE.pop(_WIRE, None)  # noqa: SLF001


def test_auto_activate_top_hits() -> None:
    """检索后自动激活 top-N：省掉 load_tool 往返；已加载/超上限/空候选跳过。"""

    from app.services.mcp.tool_search_index import _auto_activate_top_hits

    state: dict = {}
    key = "adk_tool_search.loaded_tools.ns"
    hits = [
        {"name": "tool_a", "description": "d", "score": 0.9},
        {"name": "tool_b", "description": "d", "score": 0.8},
        {"name": "tool_c", "description": "d", "score": 0.7},
        {"name": "tool_d", "description": "d", "score": 0.6},
    ]

    # 默认 top-N=3：激活前三，第四个不激活
    assert _auto_activate_top_hits(hits, state, key, max_loaded=20) == [
        "tool_a", "tool_b", "tool_c"
    ]
    assert state[key] == ["tool_a", "tool_b", "tool_c"]
    # 已加载的不重复
    assert _auto_activate_top_hits(hits, state, key, max_loaded=20) == []
    # 上限只够再放一个
    assert _auto_activate_top_hits(hits, {"k": []}, key, max_loaded=1) == ["tool_a"]
    # 空结果 / 无名候选不改
    assert _auto_activate_top_hits([], {}, key, max_loaded=20) == []
    assert _auto_activate_top_hits([{"name": ""}], {}, key, max_loaded=20) == []
