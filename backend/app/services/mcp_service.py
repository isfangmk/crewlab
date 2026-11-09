"""MCP Server 管理与探测。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.mcp import McpServer

logger = logging.getLogger(__name__)


_APPROVAL_POLICIES = frozenset({"never", "non_readonly", "always"})


def effective_approval_policy(server: McpServer) -> str:
    """trusted 视为 never；否则使用 approval_policy。"""

    if server.trusted:
        return "never"
    policy = server.approval_policy
    if policy not in _APPROVAL_POLICIES:
        raise ValueError(f"MCP Server {server.name} approval_policy 非法: {policy}")
    return policy


def server_to_wire(s: McpServer) -> dict:
    """把 ORM 行转成前端 MCP 列表/详情用的字段字典。"""

    return {
        "id": s.id,
        "name": s.name,
        "transport": s.transport,
        "endpoint": s.endpoint,
        "credential": s.credential,
        "header_template_json": s.header_template_json,
        "trusted": s.trusted,
        "approval_policy": effective_approval_policy(s),
        "enabled": s.enabled,
        "status": s.status,
        "last_error": s.last_error,
        "tools_cache_json": s.tools_cache_json,
        "embedding_sync_status": s.embedding_sync_status,
        "embedding_sync_count": s.embedding_sync_count,
        "embedding_sync_error": s.embedding_sync_error,
        "embedding_synced_at": (
            None if s.embedding_synced_at is None else s.embedding_synced_at.isoformat()
        ),
    }


def _parse_headers(raw: str) -> dict[str, str]:
    if raw == "":
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("header_template_json 必须是 JSON 对象")
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError("header_template_json 的键值必须都是字符串")
        out[k] = v
    return out


def sanitize_mcp_segment(value: str) -> str:
    """规范化 MCP **工具名**片段：保留小写字母与数字，其余转下划线。

    只用于 tool 段。server 段必须保留数据库原始名（大小写与连字符），
    否则 add-work 与 add_work 会在 wire 上撞名。
    """

    out = []
    for char in value.strip():
        if "a" <= char <= "z" or "0" <= char <= "9":
            out.append(char)
        elif "A" <= char <= "Z":
            out.append(char.lower())
        else:
            out.append("_")
    return "".join(out)


def assert_mcp_server_name(server_name: str) -> None:
    """校验 MCP server 名可作为 wire 中间段。

    wire 形如 mcp__{server}__{tool}，以双下划线分段；server 名若自身含
    `__` 会破坏解析，必须在写入时显式拒绝。
    """

    if server_name == "":
        raise ValueError("server name 不能为空")
    if "__" in server_name:
        raise ValueError(f"MCP Server 名称不能包含 '__': {server_name}")


def mcp_toolset_name_prefix(server_name: str) -> str:
    """目录/发现工具名前缀，形如 mcp__{server}__（与 wire 名规则同源）。"""

    assert_mcp_server_name(server_name)
    return f"mcp__{server_name}__"


def mcp_tool_wire_name(server_name: str, tool_name: str) -> str:
    """与前端 / 助理白名单一致的完整工具名。

    命名规则是本仓库的唯一权威：mcp__{原始 server 名}__{规范化 tool}。
    server 段保留数据库原值（可含连字符与大小写），tool 段经
    sanitize_mcp_segment 规范化。前端助理白名单、后端审批名单、
    运行时工具名必须全部用本函数生成。
    """

    if tool_name == "":
        raise ValueError("tool name 不能为空")
    assert_mcp_server_name(server_name)
    tool_seg = sanitize_mcp_segment(tool_name)
    if tool_seg == "":
        raise ValueError(f"tool name 规范化后为空: {tool_name}")
    return f"mcp__{server_name}__{tool_seg}"


def mcp_server_name_from_wire_name(wire_name: str) -> str:
    """从 wire 工具名反解出 MCP server 的原始名（与 McpServer.name 一致）。"""

    if not isinstance(wire_name, str) or wire_name == "":
        raise ValueError("wire 工具名不能为空")
    parts = wire_name.split("__")
    if len(parts) != 3 or parts[0] != "mcp":
        raise ValueError(f"非法 MCP 工具名: {wire_name}")
    if parts[1] == "" or parts[2] == "":
        raise ValueError(f"非法 MCP 工具名: {wire_name}")
    return parts[1]


def _bind_tool_wire_name(tool: Any, wire_name: str) -> None:
    """同时改写运行时 name 与 FunctionDeclaration.name。"""

    if wire_name == "":
        raise ValueError("wire_name 不能为空")
    tool.name = wire_name
    original_get = tool._get_declaration  # noqa: SLF001

    def _get_prefixed_declaration(
        _original_get=original_get, _wire_name=wire_name
    ):
        declaration = _original_get()
        if declaration is not None:
            declaration.name = _wire_name
        return declaration

    tool._get_declaration = _get_prefixed_declaration  # noqa: SLF001


def render_header_template(
    template_headers: dict[str, str], credential_fields: dict[str, str]
) -> dict[str, str]:
    """把 header 模板里的 {{字段}} 占位符替换为凭证字段值。

    模板里出现但凭证没有的字段必须显式失败；不能留空串发出去
    （那会让远端收到一个空 Authorization，错误现场极难排查）。
    没有选凭证却写了占位符，同样显式失败。
    """

    rendered: dict[str, str] = {}
    for key, raw_value in template_headers.items():
        def _sub(match: re.Match[str]) -> str:
            field = match.group(1)
            if field not in credential_fields:
                raise ValueError(
                    f"header 模板引用了凭证中不存在的字段: {{{{{field}}}}}"
                )
            value = credential_fields[field]
            if value == "":
                raise ValueError(f"凭证字段为空: {field}")
            return value

        rendered[key] = re.sub(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", _sub, raw_value)
    return rendered


def has_header_placeholder(template_headers: dict[str, str]) -> bool:
    """模板里是否含 {{字段}} 占位符。"""

    return any(
        re.search(r"\{\{\s*[A-Za-z0-9_.-]+\s*\}\}", value)
        for value in template_headers.values()
    )


async def connection_params_for_server(db: AsyncSession, server: McpServer):
    """按 Server 配置构造 ADK MCP 连接参数。

    credential 列存的是「凭证库里的凭证名」，不是密钥本身：
    这里按名取出凭证字段，把 header_template_json 里的 {{字段}}
    占位符替换成真实值后再连远端。凭证缺失 / 字段缺失都显式失败。
    """

    template_headers = _parse_headers(server.header_template_json)
    credential_fields: dict[str, str] = {}
    if server.credential != "":
        from app.services.secrets_service import resolve_secret_fields

        credential_fields = await resolve_secret_fields(db, server.credential)
    elif has_header_placeholder(template_headers):
        raise ValueError(
            f"MCP Server {server.name} 的 header 模板含 {{字段}} 占位符，但未选择凭证"
        )

    headers = render_header_template(template_headers, credential_fields)

    if server.transport == "http":
        return StreamableHTTPConnectionParams(url=server.endpoint, headers=headers)
    if server.transport == "sse":
        return SseConnectionParams(url=server.endpoint, headers=headers)
    raise ValueError(f"不支持的 MCP transport: {server.transport}")


async def build_mcp_toolset(db: AsyncSession, server: McpServer) -> McpToolset:
    """按注册信息构造 ADK McpToolset（探测/管理页直连；会 list_tools）。

    对话侧审批由 before_tool_callback 负责，此处不再设 require_confirmation。
    """

    return McpToolset(
        connection_params=await connection_params_for_server(db, server),
        require_confirmation=False,
    )


def _tool_looks_readonly(tool_name: str, description: str) -> bool:
    """粗判定只读工具：名称以 get/list/search/query/read 开头或描述含 read-only。"""

    lowered = tool_name.lower()
    for prefix in ("get_", "list_", "search_", "query_", "read_", "find_", "describe_"):
        if lowered.startswith(prefix):
            return True
    if "read-only" in description.lower() or "readonly" in description.lower():
        return True
    return False


def confirm_tool_names_for_servers(servers: list[McpServer]) -> set[str]:
    """按 approval_policy 收集需审批的业务工具 wire 名。

    - never：不进审批
    - always：全部进审批
    - non_readonly：非只读工具进审批
    """

    names: set[str] = set()
    for server in servers:
        policy = effective_approval_policy(server)
        if policy == "never":
            continue
        if server.status != "ok":
            logger.warning(
                "审批名单跳过未探测成功的 MCP Server id=%s name=%s status=%s",
                server.id,
                server.name,
                server.status,
            )
            continue
        raw = server.tools_cache_json
        if raw == "":
            raise ValueError(f"MCP Server {server.name} 工具缓存为空，请先探测")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise TypeError(f"MCP Server {server.name} tools_cache_json 必须是数组")
        if len(data) == 0:
            raise ValueError(f"MCP Server {server.name} 工具缓存为空，请先探测")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise TypeError(f"tools_cache[{i}] 必须是对象")
            tool_name = item["name"]
            if not isinstance(tool_name, str) or tool_name == "":
                raise ValueError(f"tools_cache[{i}].name 无效")
            desc = item.get("description")
            if not isinstance(desc, str):
                desc = ""
            if policy == "non_readonly" and _tool_looks_readonly(tool_name, desc):
                continue
            names.add(mcp_tool_wire_name(server.name, tool_name))
    logger.info("需审批 MCP 工具数=%s", len(names))
    return names


def all_business_tool_names_for_servers(servers: list[McpServer]) -> set[str]:
    """所有可用 MCP server 的业务工具 wire 名（含 trusted）。

    助理只读模式用：把所有工具都拉进审批名单。
    与 confirm_tool_names_for_servers 的可用性判定保持一致
    （探测未成功的 server 跳过）。
    """

    names: set[str] = set()
    for server in servers:
        if server.status != "ok":
            logger.warning(
                "工具名单跳过未探测成功的 MCP Server id=%s name=%s status=%s",
                server.id,
                server.name,
                server.status,
            )
            continue
        raw = server.tools_cache_json
        if raw == "":
            raise ValueError(f"MCP Server {server.name} 工具缓存为空，请先探测")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise TypeError(f"MCP Server {server.name} tools_cache_json 必须是数组")
        if len(data) == 0:
            raise ValueError(f"MCP Server {server.name} 工具缓存为空，请先探测")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise TypeError(f"tools_cache[{i}] 必须是对象")
            tool_name = item["name"]
            if not isinstance(tool_name, str) or tool_name == "":
                raise ValueError(f"tools_cache[{i}].name 无效")
            names.add(mcp_tool_wire_name(server.name, tool_name))
    return names


async def build_searchable_mcp_toolsets(
    db: AsyncSession, servers: list[McpServer]
) -> list:
    """MCP 工具不进初始上下文：经 SearchableToolset 搜索后再加载调用。

    目录来自 DB tools_cache（CachedMcpCatalogToolset），发现阶段不连远程；
    检索用 EmbeddingToolIndex。
    """

    from app.services.mcp_catalog_toolset import CachedMcpCatalogToolset
    from app.services.tool_search_index import (
        EmbeddingToolIndex,
        PrefetchingEmbeddingToolset,
    )

    toolsets = []
    skipped: list[str] = []
    for server in servers:
        # 启用但未探测成功的 server 不可用：显式跳过并记日志，
        # 不能让整个对话装配失败（其余 server 与内置工具仍可用）
        if server.status != "ok":
            skipped.append(server.name)
            logger.warning(
                "跳过未探测成功的 MCP Server id=%s name=%s status=%s last_error=%s",
                server.id,
                server.name,
                server.status,
                server.last_error,
            )
            continue
        # 业务工具 wire 名在 source 上；SearchableToolset 不再叠前缀
        source = await CachedMcpCatalogToolset.create(db, server)
        prefix = mcp_toolset_name_prefix(server.name)
        searchable = PrefetchingEmbeddingToolset(
            namespace=f"mcp__{server.name}",
            source=source,
            tool_name_prefix=None,
            # EmbeddingToolIndex 与 BM25 同契约；上游类型写死了 BM25
            index_factory=cast(Any, EmbeddingToolIndex),
        )
        # 管理工具单独加前缀，且同步声明名（只改 name 会导致模型仍调用 search_tools）
        for discovery in searchable._discovery_tools:  # noqa: SLF001
            _bind_tool_wire_name(discovery, f"{prefix}{discovery.name}")
        toolsets.append(searchable)
        logger.info(
            "装配可搜索 MCP toolset id=%s name=%s prefix=%s trusted=%s index=embedding source=cache",
            server.id,
            server.name,
            prefix,
            server.trusted,
        )
    if skipped:
        logger.warning("本次装配跳过不可用的 MCP Server names=%s", skipped)
    return toolsets


async def list_servers(db: AsyncSession) -> list[McpServer]:
    result = await db.scalars(select(McpServer).order_by(McpServer.id.asc()))
    return list(result.all())


async def list_enabled_servers(db: AsyncSession) -> list[McpServer]:
    result = await db.scalars(
        select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.id.asc())
    )
    return list(result.all())


async def get_server(db: AsyncSession, server_id: int) -> McpServer:
    row = await db.get(McpServer, server_id)
    if row is None:
        raise LookupError(f"MCP Server 不存在: {server_id}")
    return row


async def create_server(
    db: AsyncSession,
    *,
    name: str,
    transport: str,
    endpoint: str,
    credential: str,
    header_template_json: str,
    trusted: bool,
    enabled: bool,
    approval_policy: str = "always",
) -> McpServer:
    if transport not in ("http", "sse"):
        raise ValueError(f"不支持的 transport: {transport}")
    if endpoint == "":
        raise ValueError("endpoint 不能为空")
    if approval_policy not in _APPROVAL_POLICIES:
        raise ValueError(f"approval_policy 非法: {approval_policy}")
    assert_mcp_server_name(name)
    _parse_headers(header_template_json)
    # 名称唯一：显式预检，避免 IntegrityError 变成 500
    existing = await db.scalar(select(McpServer).where(McpServer.name == name))
    if existing is not None:
        raise ValueError(f"MCP Server 名称已存在: {name}")
    row = McpServer(
        name=name,
        transport=transport,
        endpoint=endpoint,
        credential=credential,
        header_template_json=header_template_json,
        trusted=trusted,
        approval_policy="never" if trusted else approval_policy,
        enabled=enabled,
        status="",
        last_error="",
        tools_cache_json="[]",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("创建 MCP Server id=%s name=%s transport=%s", row.id, row.name, row.transport)
    # 新建后立刻探测工具并同步向量库；失败则回滚删除该 Server，避免半成品
    try:
        await test_server(db, row.id)
    except Exception:
        logger.exception("新建 MCP 后同步向量失败，回滚删除 id=%s", row.id)
        await delete_server(db, row.id)
        raise
    await db.refresh(row)
    return row


async def update_server(
    db: AsyncSession,
    server_id: int,
    *,
    name: str,
    transport: str,
    endpoint: str,
    credential: str,
    header_template_json: str,
    trusted: bool,
    enabled: bool,
    approval_policy: str = "always",
) -> McpServer:
    row = await get_server(db, server_id)
    if transport not in ("http", "sse"):
        raise ValueError(f"不支持的 transport: {transport}")
    if endpoint == "":
        raise ValueError("endpoint 不能为空")
    if approval_policy not in _APPROVAL_POLICIES:
        raise ValueError(f"approval_policy 非法: {approval_policy}")
    assert_mcp_server_name(name)
    _parse_headers(header_template_json)
    # 改名时同样校验唯一性
    duplicate = await db.scalar(
        select(McpServer).where(McpServer.name == name, McpServer.id != server_id)
    )
    if duplicate is not None:
        raise ValueError(f"MCP Server 名称已存在: {name}")
    row.name = name
    row.transport = transport
    row.endpoint = endpoint
    row.credential = credential
    row.header_template_json = header_template_json
    row.trusted = trusted
    row.approval_policy = "never" if trusted else approval_policy
    row.enabled = enabled
    await db.commit()
    await db.refresh(row)
    logger.info("更新 MCP Server id=%s", row.id)
    # 配置变更后重新探测并同步向量
    await test_server(db, row.id)
    await db.refresh(row)
    return row


async def delete_server(db: AsyncSession, server_id: int) -> None:
    row = await get_server(db, server_id)
    # 先清进程缓存：向量行随 FK CASCADE 删除，但缓存不失效会让已删工具继续参与检索
    from app.services.tool_search_index import forget_server_vectors

    await forget_server_vectors(db, server_id)
    await db.delete(row)
    await db.commit()
    logger.info("删除 MCP Server id=%s（工具向量随 FK CASCADE 清理）", server_id)


def _tool_to_wire(tool: Any) -> dict:
    """把 ADK MCP Tool 转成前端缓存结构（含 input_schema）。"""

    raw = getattr(tool, "raw_mcp_tool", None)
    name = getattr(raw, "name", None) if raw is not None else None
    if not isinstance(name, str) or name == "":
        name = getattr(tool, "name", None)
    if not isinstance(name, str) or name == "":
        raise TypeError("MCP tool 缺少 name")

    description = getattr(tool, "description", "")
    if not isinstance(description, str):
        description = ""

    input_schema: dict | None = None
    if raw is not None:
        schema = getattr(raw, "input_schema", None)
        if isinstance(schema, dict):
            input_schema = schema
    if input_schema is None and hasattr(tool, "_get_declaration"):
        decl = tool._get_declaration()  # noqa: SLF001 — ADK 官方声明入口
        schema = getattr(decl, "parameters_json_schema", None)
        if isinstance(schema, dict):
            input_schema = schema
    if input_schema is None:
        raise TypeError(f"MCP tool {name} 缺少 input_schema")

    return {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }


def _format_call_tool_result(result: object) -> str:
    """把 MCP call_tool 结果压成可复制的文本。"""

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, indent=2)

    content = getattr(result, "content", None)
    if content is None:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    texts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
        else:
            texts.append(json.dumps(block, ensure_ascii=False, default=str))
    if len(texts) == 1:
        return texts[0]
    return "\n".join(texts)


async def test_server(db: AsyncSession, server_id: int) -> list[dict]:
    from datetime import datetime, timezone

    from app.services.tool_search_index import sync_server_tool_embeddings

    row = await get_server(db, server_id)
    row.embedding_sync_status = "syncing"
    row.embedding_sync_error = ""
    await db.commit()
    toolset = await build_mcp_toolset(db, row)
    try:
        tools = await toolset.get_tools()
        items = [_tool_to_wire(t) for t in tools]
        row.status = "ok"
        row.last_error = ""
        row.tools_cache_json = json.dumps(items, ensure_ascii=False)
        await db.commit()
        logger.info("探测 MCP 成功 id=%s tools=%s", row.id, len(items))
        # 向量按运行时完整工具名入库，与 SearchableToolset 索引文本一致
        embed_items = [
            {
                "name": mcp_tool_wire_name(row.name, item["name"]),
                "description": item["description"],
                "input_schema": item["input_schema"],
            }
            for item in items
        ]
        await sync_server_tool_embeddings(db, row.id, embed_items)
        row.embedding_sync_status = "ok"
        row.embedding_sync_count = len(embed_items)
        row.embedding_sync_error = ""
        row.embedding_synced_at = datetime.now(timezone.utc)
        await db.commit()
        return items
    except Exception as exc:
        row.status = "error"
        row.last_error = str(exc)
        row.embedding_sync_status = "error"
        row.embedding_sync_error = str(exc)
        await db.commit()
        logger.exception("探测 MCP 失败 id=%s", row.id)
        raise
    finally:
        await toolset.close()


async def run_tool(
    db: AsyncSession,
    server_id: int,
    tool_name: str,
    arguments: dict,
) -> dict:
    """在管理页直接调用 MCP 工具（不经 Agent / 审批）。"""

    if tool_name == "":
        raise ValueError("tool_name 不能为空")

    row = await get_server(db, server_id)
    toolset = await build_mcp_toolset(db, row)
    try:
        session = await toolset._mcp_session_manager.create_session()  # noqa: SLF001
        logger.info(
            "管理页运行 MCP 工具 server_id=%s name=%s tool=%s arg_keys=%s",
            row.id,
            row.name,
            tool_name,
            sorted(arguments.keys()),
        )
        result = await session.call_tool(tool_name, arguments)
        text = _format_call_tool_result(result)
        is_error = bool(getattr(result, "isError", False))
        logger.info(
            "管理页 MCP 工具完成 server_id=%s tool=%s is_error=%s bytes=%s",
            row.id,
            tool_name,
            is_error,
            len(text),
        )
        return {
            "tool": tool_name,
            "is_error": is_error,
            "result_text": text,
        }
    except Exception:
        logger.exception(
            "管理页运行 MCP 工具失败 server_id=%s tool=%s",
            row.id,
            tool_name,
        )
        raise
    finally:
        await toolset.close()
