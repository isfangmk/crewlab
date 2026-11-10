"""基于 DB 工具缓存的 MCP 目录：发现不连网，执行才 call_tool。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool.mcp_tool import McpTool
from google.adk.tools.mcp_tool.mcp_session_manager import MCPSessionManager
from mcp.types import Tool as McpSdkTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.mcp import McpServer

logger = logging.getLogger(__name__)


def _parse_cached_tools(raw: str, *, server_name: str) -> list[dict]:
    if raw == "":
        raise ValueError(f"MCP Server {server_name} 工具缓存为空，请先探测")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError(f"MCP Server {server_name} tools_cache_json 必须是数组")
    if len(data) == 0:
        raise ValueError(f"MCP Server {server_name} 工具缓存为空，请先探测")
    items: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise TypeError(f"tools_cache[{i}] 必须是对象")
        name = item["name"]
        description = item["description"]
        schema = item["input_schema"]
        if not isinstance(name, str) or name == "":
            raise ValueError(f"tools_cache[{i}].name 无效")
        if not isinstance(description, str):
            raise TypeError(f"tools_cache[{i}].description 必须是字符串")
        if not isinstance(schema, dict):
            raise TypeError(f"tools_cache[{i}].input_schema 必须是对象")
        items.append(
            {"name": name, "description": description, "input_schema": schema}
        )
    return items


class CachedMcpCatalogToolset(BaseToolset):
    """目录来自 tools_cache_json；仅工具真正执行时经 MCPSessionManager 连远程。"""

    def __init__(self, server: McpServer, connection_params) -> None:
        super().__init__()
        if server.name == "":
            raise ValueError("server name 不能为空")
        if server.status != "ok":
            raise ValueError(
                f"MCP Server {server.name} 状态不是 ok（status={server.status!r}），请先探测"
            )
        from app.services.mcp_service import mcp_tool_wire_name

        cached = _parse_cached_tools(server.tools_cache_json, server_name=server.name)
        self._server_id = server.id
        self._server_name = server.name
        self._session_manager = MCPSessionManager(connection_params=connection_params)
        tools: list[BaseTool] = []
        for item in cached:
            # SDK name 保持裸名（远程 call_tool 用）；ADK 暴露名用 wire name
            # 审批由 LlmAgent.before_tool_callback 统一闸门，不在工具上设 confirmation
            mcp_tool = McpSdkTool(
                name=item["name"],
                description=item["description"],
                input_schema=item["input_schema"],
            )
            adk_tool = McpTool(
                mcp_tool=mcp_tool,
                mcp_session_manager=self._session_manager,
                require_confirmation=False,
            )
            adk_tool.name = mcp_tool_wire_name(server.name, item["name"])
            tools.append(adk_tool)
        self._tools = tools
        logger.info(
            "已从工具缓存装配 MCP 目录 server_id=%s name=%s tools=%s trusted=%s",
            self._server_id,
            self._server_name,
            len(self._tools),
            server.trusted,
        )

    @classmethod
    async def create(
        cls, db: AsyncSession, server: McpServer
    ) -> "CachedMcpCatalogToolset":
        """构造目录 toolset：先解析连接参数（含凭证占位符替换）。"""

        from app.services.mcp_service import connection_params_for_server

        params = await connection_params_for_server(db, server)
        return cls(server, params)

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> list[BaseTool]:
        return list(self._tools)

    async def close(self) -> None:
        await self._session_manager.close()
        logger.info(
            "已关闭缓存目录 MCP session server_id=%s name=%s",
            self._server_id,
            self._server_name,
        )


class McpToolsView(BaseToolset):
    """按 wire 名裁剪一份 MCP 目录视图，生命周期仍归底层 toolset。

    用途：技能可以把部分（或全部）MCP 工具声明为「直接可见」。
    直接可见的工具不能同时出现在检索目录里（SearchableToolset 会拒绝
    既 always_available 又可检索的同名工具），所以先把它们从底层视图里
    摘掉；全部被声明时只剩这个视图，连 search/load 都不再暴露。
    close 必须透传，否则 MCP session manager 不会被释放。
    """

    def __init__(
        self,
        source: BaseToolset,
        *,
        visible: set[str] | None = None,
        hidden: set[str] | None = None,
    ) -> None:
        super().__init__()
        if visible is None and hidden is None:
            raise ValueError("McpToolsView 至少要给 visible 或 hidden")
        if visible is not None and hidden is not None:
            raise ValueError("McpToolsView 的 visible 与 hidden 只能二选一")
        self._source = source
        self._visible = visible
        self._hidden = set() if hidden is None else set(hidden)

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> list[BaseTool]:
        tools = await self._source.get_tools(readonly_context)
        if self._visible is not None:
            return [tool for tool in tools if tool.name in self._visible]
        return [tool for tool in tools if tool.name not in self._hidden]

    async def close(self) -> None:
        await self._source.close()
