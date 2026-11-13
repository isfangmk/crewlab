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


def parse_cached_tools(
    raw: str,
    *,
    server_name: str,
    require_schema: bool = True,
) -> list[dict]:
    """解析并校验 tools_cache_json；全平台的唯一入口。

    各处手写的等价解析（审批名单、工具目录、工作流编排）已经漂移出
    三种错误文案与两种宽松度，这里统一：缓存为空或缺结构都显式失败。
    require_schema=False 供只取名字/描述的调用方使用。
    """

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
        description = item.get("description", "")
        schema = item.get("input_schema")
        if not isinstance(name, str) or name == "":
            raise ValueError(f"tools_cache[{i}].name 无效")
        if not isinstance(description, str):
            raise TypeError(f"tools_cache[{i}].description 必须是字符串")
        if require_schema and not isinstance(schema, dict):
            raise TypeError(
                f"MCP Server {server_name} 工具 {name} 缺少 input_schema，请重新测试连接"
            )
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

        cached = parse_cached_tools(server.tools_cache_json, server_name=server.name)
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

    用途：助理白名单只勾了 Server 内部分工具时，检索目录只能包含这些工具——
    未勾选的工具出现在检索结果里，白名单就形同虚设（模型能搜到并调用用户
    明确排除的工具）。
    close 必须透传，否则 MCP session manager 不会被释放。
    """

    def __init__(self, source: BaseToolset, *, visible: set[str]) -> None:
        super().__init__()
        self._source = source
        self._visible = set(visible)

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> list[BaseTool]:
        tools = await self._source.get_tools(readonly_context)
        return [tool for tool in tools if tool.name in self._visible]

    async def close(self) -> None:
        await self._source.close()
