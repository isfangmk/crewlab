"""MCP Registry 代理：搜索官方注册表并映射为登记表单预填结构。

来源 registry.modelcontextprotocol.io；网络不可达时显式抛错，前端展示
错误横幅并给重试入口，不做静默降级。OAuth / Apps / Tasks 不在本轮范围。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
REQUEST_TIMEOUT = 15.0
MAX_LIMIT = 50


def _suggest_name(raw: str) -> str:
    """注册表名转 Server 名：不能含 '__'（wire 命名约束），超长截断。"""

    name = raw.replace("__", "-").strip()
    return name[:128]


def registry_entry_to_server_input(entry: dict[str, Any]) -> dict[str, Any]:
    """注册表条目 → 登记表单预填：优先 remote URL，包形式给部署提示。

    线上注册表条目带 `server` 包装（2025-12-11 schema），这里先解包；
    老的扁平条目结构保持兼容。
    """

    inner = entry.get("server")
    if isinstance(inner, dict):
        entry = inner
    name = _suggest_name(str(entry.get("name") or ""))
    description = str(entry.get("description") or "")

    for remote in entry.get("remotes") or []:
        if not isinstance(remote, dict):
            continue
        url = str(remote.get("url") or "")
        if url == "":
            continue
        # 传输只支持 http/stdio；registry 的 sse 条目也按 http 处理
        transport = "http"
        return {
            "name": name,
            "description": description,
            "transport": transport,
            "endpoint": url,
            "hint": "",
        }

    hints: list[str] = []
    for pkg in entry.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        identifier = str(pkg.get("identifier") or "")
        registry_type = str(pkg.get("registryType") or "")
        if identifier != "":
            hints.append(f"{registry_type} {identifier}".strip())
    joined = "、".join(hints) if hints else "见注册表条目"
    return {
        "name": name,
        "description": description,
        "transport": "http",
        "endpoint": "",
        "hint": f"包形式发布（{joined}），需自行部署后填写 endpoint",
    }


async def search_registry(
    *, query: str = "", limit: int = 20, cursor: str = ""
) -> dict[str, Any]:
    """代理注册表搜索；返回映射后的预填项与下一页游标。"""

    params: dict[str, Any] = {"limit": max(1, min(limit, MAX_LIMIT))}
    if query.strip() != "":
        params["search"] = query.strip()
    if cursor.strip() != "":
        params["cursor"] = cursor.strip()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{REGISTRY_BASE}/v0/servers", params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise TypeError("Registry 响应必须是 JSON 对象")
    servers = data.get("servers")
    if not isinstance(servers, list):
        raise TypeError("Registry 响应缺少 servers 数组")
    metadata = data.get("metadata")
    next_cursor = ""
    if isinstance(metadata, dict):
        # 线上注册表用驼峰 nextCursor；保留 snake 兼容
        next_cursor = str(metadata.get("nextCursor") or metadata.get("next_cursor") or "")
    items = [
        registry_entry_to_server_input(entry)
        for entry in servers
        if isinstance(entry, dict)
    ]
    logger.info("Registry 搜索 query=%r hits=%s", query, len(items))
    return {"items": items, "next_cursor": next_cursor, "total": len(items)}
