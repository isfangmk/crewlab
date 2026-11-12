"""内置 Tool 目录与手动运行（侧边栏 /tools）。"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.web_search_service import deepseek_web_search, resolve_deepseek_search_cred
from app.tools.catalog import BUILTIN_TOOL_SPECS, tool_impl, tool_param_specs

logger = logging.getLogger(__name__)

# 目录分组由后端提供，前端只按 key 渲染；标题同时给中英文，跟随 UI 语言
_BUILTIN_TOOL_GROUPS: dict[str, dict[str, str]] = {
    "knowledge": {"title": "知识检索", "title_en": "Knowledge"},
    "search": {"title": "联网搜索", "title_en": "Web search"},
    "workflow": {"title": "工作流", "title_en": "Workflow"},
}


def list_builtin_tools() -> list[dict]:
    """目录项带参数定义，供前端渲染运行表单。"""

    return [
        {
            "name": spec["name"],
            "group": spec["group"],
            "description": spec["description"],
            "risk": spec["risk"],
            "mount": spec["mount"],
            "params": tool_param_specs(tool_impl(spec["name"])),
        }
        for spec in BUILTIN_TOOL_SPECS
    ]


def list_builtin_tool_groups() -> list[dict[str, str]]:
    """按注册顺序返回目录实际使用的分组及其标题。"""

    groups: list[dict[str, str]] = []
    seen: set[str] = set()
    for spec in BUILTIN_TOOL_SPECS:
        key = spec["group"]
        if key in seen:
            continue
        meta = _BUILTIN_TOOL_GROUPS.get(key)
        if meta is None:
            raise RuntimeError(f"内置工具分组缺少标题: {key}")
        groups.append({"key": key, "title": meta["title"], "title_en": meta["title_en"]})
        seen.add(key)
    return groups


def _validate_params(name: str, params: dict[str, Any]) -> dict[str, Any]:
    specs = tool_param_specs(tool_impl(name))
    allowed = {p["name"] for p in specs}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"工具 {name} 不接受参数: {','.join(unknown)}")

    kwargs: dict[str, Any] = {}
    for spec in specs:
        pname = spec["name"]
        if pname in params:
            kwargs[pname] = params[pname]
            continue
        if spec.get("required"):
            raise ValueError(f"工具 {name} 缺少必填参数: {pname}")
    return kwargs


async def run_builtin_tool(
    db: AsyncSession,
    name: str,
    params: dict[str, Any],
) -> dict:
    """按名字执行内置 Tool；未知名 / 非法参数显式失败。"""

    if name == "":
        raise ValueError("tool name 不能为空")
    tool = tool_impl(name)
    kwargs = _validate_params(name, params)
    logger.info("手动运行内置工具 name=%s params=%s", name, sorted(kwargs))

    if name == "web_search":
        base_url, api_key = await resolve_deepseek_search_cred(db)
        result = await deepseek_web_search(base_url=base_url, api_key=api_key, **kwargs)
        return {"tool": name, "result": result}

    result = tool(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return {"tool": name, "result": result}
