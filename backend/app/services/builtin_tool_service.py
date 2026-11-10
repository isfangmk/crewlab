"""内置 Tool 目录与手动运行（侧边栏 /tools）。"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.builtin_tools import (
    BUILTIN_TOOL_SPECS,
    query_knowledge,
    tool_param_specs,
)
from app.services.web_search_service import deepseek_web_search, resolve_deepseek_search_cred

logger = logging.getLogger(__name__)


async def _web_search_signature(query: str, count: int = 5) -> dict:
    """仅用于导出参数签名；真正执行走 run_builtin_tool。"""

    raise RuntimeError("web_search 须经 run_builtin_tool 注入 DeepSeek 凭证后执行")


def _param_source(name: str) -> Any:
    if name == "query_knowledge":
        return query_knowledge
    if name == "web_search":
        return _web_search_signature
    raise LookupError(f"未知内置工具: {name}")


def list_builtin_tools() -> list[dict]:
    """目录项带参数定义，供前端渲染运行表单。"""

    items: list[dict] = []
    for spec in BUILTIN_TOOL_SPECS:
        name = spec["name"]
        items.append(
            {
                "name": name,
                "description": spec["description"],
                "risk": spec["risk"],
                "mount": spec["mount"],
                "params": tool_param_specs(_param_source(name)),
            }
        )
    return items


def _validate_params(name: str, params: dict[str, Any]) -> dict[str, Any]:
    specs = tool_param_specs(_param_source(name))
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
    known = {s["name"] for s in BUILTIN_TOOL_SPECS}
    if name not in known:
        raise LookupError(f"内置工具不存在: {name}")

    kwargs = _validate_params(name, params)
    logger.info("手动运行内置工具 name=%s params=%s", name, sorted(kwargs))

    if name == "query_knowledge":
        result = query_knowledge(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return {"tool": name, "result": result}

    if name == "web_search":
        base_url, api_key = await resolve_deepseek_search_cred(db)
        result = await deepseek_web_search(base_url=base_url, api_key=api_key, **kwargs)
        return {"tool": name, "result": result}

    # 目录有、但未实现运行分支：显式失败，禁止静默跳过
    raise LookupError(f"内置工具不可手动运行: {name}")
