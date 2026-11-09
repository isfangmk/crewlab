"""用户自定义助理 CRUD，以及内置 default 解析。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.agent import UserAgent
from app.models_orm.mcp import McpServer

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

DEFAULT_AGENT_NAME = "default"

# 允许的权限模式：
# "" = 不限（默认）
# "read-only" = 只读：该助理调用的 MCP 工具一律需要人工确认，不允许静默产生副作用
PERMISSION_MODES = ("", "read-only")


@dataclass(frozen=True)
class ResolvedAgent:
    """运行时解析后的助理配置。"""

    name: str
    description: str
    when_to_use: str
    system_prompt: str
    critical_reminder: str
    tools: list[str]
    disallowed_tools: list[str]
    permission_mode: str
    model: str
    max_turns: int
    source: str


def _parse_str_list(raw: str, field: str) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError(f"{field} 必须是 JSON 数组")
    out: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str) or item == "":
            raise TypeError(f"{field}[{i}] 必须是非空字符串")
        out.append(item)
    return out


def _dump_str_list(items: list[str], field: str) -> str:
    for i, item in enumerate(items):
        if not isinstance(item, str) or item == "":
            raise ValueError(f"{field}[{i}] 必须是非空字符串")
    return json.dumps(items, ensure_ascii=False)


def agent_to_wire(agent: ResolvedAgent) -> dict:
    """把解析后的助理转成列表/详情响应字段。"""

    return {
        "name": agent.name,
        "description": agent.description,
        "when_to_use": agent.when_to_use,
        "tools": agent.tools,
        "disallowed_tools": agent.disallowed_tools,
        "permission_mode": agent.permission_mode,
        "model": agent.model,
        "max_turns": agent.max_turns,
        "system_prompt": agent.system_prompt,
        "critical_reminder": agent.critical_reminder,
        "source": agent.source,
    }


def builtin_default_agent() -> ResolvedAgent:
    """工作台内置默认助理（不落库）。"""

    return ResolvedAgent(
        name=DEFAULT_AGENT_NAME,
        description="默认助理，适合通用问答与临时排查。",
        when_to_use="未指定专用助理时使用。",
        system_prompt="",
        critical_reminder="",
        tools=[],
        disallowed_tools=[],
        permission_mode="",
        model="",
        max_turns=0,
        source="builtin",
    )


def _row_to_resolved(row: UserAgent) -> ResolvedAgent:
    return ResolvedAgent(
        name=row.name,
        description=row.description,
        when_to_use=row.when_to_use,
        system_prompt=row.system_prompt,
        critical_reminder=row.critical_reminder,
        tools=_parse_str_list(row.allowed_tools_json, "allowed_tools_json"),
        disallowed_tools=_parse_str_list(row.disallowed_tools_json, "disallowed_tools_json"),
        permission_mode=row.permission_mode,
        model=row.model,
        max_turns=row.max_turns,
        source="user",
    )


def validate_agent_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ValueError("name 须为小写字母开头的标识（字母/数字/_/-，最长 64）")
    if name == DEFAULT_AGENT_NAME:
        raise ValueError("name 不能为保留名 default")


def validate_permission_mode(mode: str) -> None:
    """权限模式取值必须已知；未知值不能被静默当成「不限」。"""

    if mode not in PERMISSION_MODES:
        raise ValueError(
            f"permission_mode 非法: {mode!r}（允许："
            f"{', '.join(repr(m) for m in PERMISSION_MODES if m != '')} 或空串）"
        )


def agent_disallowed_tool_names(agent: ResolvedAgent) -> set[str]:
    """助理级禁用工具名集合（wire 名）。"""

    names: set[str] = set()
    for key in agent.disallowed_tools:
        if not isinstance(key, str) or key == "":
            raise ValueError("disallowed_tools 含无效工具名")
        names.add(key)
    return names


async def list_agents(db: AsyncSession) -> list[ResolvedAgent]:
    result = await db.scalars(select(UserAgent).order_by(UserAgent.name.asc()))
    rows = list(result.all())
    items = [builtin_default_agent()]
    items.extend(_row_to_resolved(r) for r in rows)
    return items


async def get_agent(db: AsyncSession, name: str) -> ResolvedAgent:
    if name == DEFAULT_AGENT_NAME:
        return builtin_default_agent()
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    return _row_to_resolved(row)


async def create_user_agent(
    db: AsyncSession,
    *,
    name: str,
    description: str,
    when_to_use: str,
    system_prompt: str,
    critical_reminder: str,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    permission_mode: str,
    model: str,
    max_turns: int,
) -> ResolvedAgent:
    validate_agent_name(name)
    if description == "":
        raise ValueError("description 不能为空")
    if system_prompt == "":
        raise ValueError("system_prompt 不能为空")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    validate_permission_mode(permission_mode)
    existing = await db.get(UserAgent, name)
    if existing is not None:
        raise ValueError(f"助理已存在: {name}")
    row = UserAgent(
        name=name,
        description=description,
        when_to_use=when_to_use,
        system_prompt=system_prompt,
        critical_reminder=critical_reminder,
        allowed_tools_json=_dump_str_list(allowed_tools, "allowed_tools"),
        disallowed_tools_json=_dump_str_list(disallowed_tools, "disallowed_tools"),
        permission_mode=permission_mode,
        model=model,
        max_turns=max_turns,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("创建用户助理 name=%s tools=%s", name, len(allowed_tools))
    return _row_to_resolved(row)


async def update_user_agent(
    db: AsyncSession,
    name: str,
    *,
    description: str,
    when_to_use: str,
    system_prompt: str,
    critical_reminder: str,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    permission_mode: str,
    model: str,
    max_turns: int,
) -> ResolvedAgent:
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    if description == "":
        raise ValueError("description 不能为空")
    if system_prompt == "":
        raise ValueError("system_prompt 不能为空")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    validate_permission_mode(permission_mode)
    row.description = description
    row.when_to_use = when_to_use
    row.system_prompt = system_prompt
    row.critical_reminder = critical_reminder
    row.allowed_tools_json = _dump_str_list(allowed_tools, "allowed_tools")
    row.disallowed_tools_json = _dump_str_list(disallowed_tools, "disallowed_tools")
    row.permission_mode = permission_mode
    row.model = model
    row.max_turns = max_turns
    await db.commit()
    await db.refresh(row)
    logger.info("更新用户助理 name=%s", name)
    return _row_to_resolved(row)


async def delete_user_agent(db: AsyncSession, name: str) -> None:
    if name == DEFAULT_AGENT_NAME:
        raise ValueError("不能删除内置助理 default")
    row = await db.get(UserAgent, name)
    if row is None:
        raise LookupError(f"助理不存在: {name}")
    await db.delete(row)
    await db.commit()
    logger.info("删除用户助理 name=%s", name)


def mcp_server_names_from_tools(tools: list[str]) -> set[str] | None:
    """从 tools 白名单提取 MCP server 名。

    返回 None 表示未限制（空白名单 = 继承全部 MCP）。
    返回空 set 表示白名单非空但不含任何 mcp__* 项。
    """

    from app.services.mcp_service import mcp_server_name_from_wire_name

    if len(tools) == 0:
        return None
    names: set[str] = set()
    for key in tools:
        if not key.startswith("mcp__"):
            continue
        names.add(mcp_server_name_from_wire_name(key))
    return names


def filter_mcp_servers_for_agent(
    servers: list[McpServer],
    agent: ResolvedAgent,
) -> list[McpServer]:
    """按助理 tools 白名单筛选可挂载的 MCP Server。"""

    allowed_names = mcp_server_names_from_tools(agent.tools)
    if allowed_names is None:
        return servers
    # wire 的 server 段就是 McpServer.name 原值，直接比对。
    filtered = [
        s for s in servers if s.name in allowed_names
    ]
    logger.info(
        "按助理筛选 MCP agent=%s allowed_servers=%s matched=%s",
        agent.name,
        sorted(allowed_names),
        [s.name for s in filtered],
    )
    return filtered
