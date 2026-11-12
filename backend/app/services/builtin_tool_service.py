"""内置 Tool 目录与手动运行（侧边栏 /tools）。"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.catalog import BUILTIN_TOOL_SPECS, tool_param_specs
from app.tools.knowledge import query_knowledge
from app.tools.workflow import (
    get_workflow,
    get_workflow_run,
    list_workflow_runs,
    list_workflows,
    run_workflow,
)
from app.services.web_search_service import deepseek_web_search, resolve_deepseek_search_cred

logger = logging.getLogger(__name__)

# 目录分组由后端提供，前端只按 key 渲染；标题同时给中英文，跟随 UI 语言。
# key 按能力主题取，不用「内置」这种笼统说法：目录里本来就是内置工具，
# 这个层级要告诉用户「这是哪一类能力」。
_BUILTIN_TOOL_GROUPS: dict[str, dict[str, str]] = {
    "knowledge": {"title": "知识检索", "title_en": "Knowledge"},
    "search": {"title": "联网搜索", "title_en": "Web search"},
    "workflow": {"title": "工作流", "title_en": "Workflow"},
}


async def _web_search_signature(
    # 参数名与默认值就是前端运行表单的字段定义（tool_param_specs 读 inspect.signature），
    # 函数体不读它们也不能改名 / 删除，因此逐参数标注忽略未使用告警。
    query: str,  # pyright: ignore[reportUnusedParameter]
    count: int = 5,  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """仅用于导出参数签名；真正执行走 run_builtin_tool。"""

    raise RuntimeError("web_search 须经 run_builtin_tool 注入 DeepSeek 凭证后执行")


def _param_source(name: str) -> Any:
    if name == "query_knowledge":
        return query_knowledge
    if name == "web_search":
        return _web_search_signature
    workflow_tools = {
        "list_workflows": list_workflows,
        "get_workflow": get_workflow,
        "run_workflow": run_workflow,
        "list_workflow_runs": list_workflow_runs,
        "get_workflow_run": get_workflow_run,
    }
    if name in workflow_tools:
        return workflow_tools[name]
    raise LookupError(f"未知内置工具: {name}")


def list_builtin_tools() -> list[dict]:
    """目录项带参数定义，供前端渲染运行表单。"""

    items: list[dict] = []
    for spec in BUILTIN_TOOL_SPECS:
        name = spec["name"]
        items.append(
            {
                "name": name,
                "group": spec["group"],
                "description": spec["description"],
                "risk": spec["risk"],
                "mount": spec["mount"],
                "params": tool_param_specs(_param_source(name)),
            }
        )
    return items


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

    if name == "list_workflow_runs":
        # 该工具自带会话：ADK 调用时拿不到请求级 db，为了两种入口共用一份实现
        result = await list_workflow_runs(**kwargs)
        return {"tool": name, "result": result}

    if name in ("list_workflows", "get_workflow", "run_workflow", "get_workflow_run"):
        tool = {
            "list_workflows": list_workflows,
            "get_workflow": get_workflow,
            "run_workflow": run_workflow,
            "get_workflow_run": get_workflow_run,
        }[name]
        result = await tool(**kwargs)
        return {"tool": name, "result": result}

    # 目录有、但未实现运行分支：显式失败，禁止静默跳过
    raise LookupError(f"内置工具不可手动运行: {name}")
