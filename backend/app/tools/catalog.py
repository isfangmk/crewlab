"""内置工具的目录声明、注册表与参数导出。

这里只描述「有哪些工具、怎么分组、怎么展示参数」，
工具本身的行为在各主题模块里；ADK 装配在 adk_runtime 里。
"""

from __future__ import annotations

import inspect
from typing import Any

from app.tools.knowledge import query_knowledge
from app.tools.workflow import (
    get_workflow,
    get_workflow_run,
    list_workflow_runs,
    list_workflows,
    run_workflow,
)

# mount 说明装配时机：always = 随 SkillToolset 始终装配；per_turn = 由对话开关决定
BUILTIN_TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "query_knowledge",
        "group": "knowledge",
        "description": "语义检索组织知识库",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "web_search",
        "group": "search",
        "description": "DeepSeek 官方网页搜索（对话开启联网时可用）",
        "risk": "safe",
        "mount": "per_turn",
    },
    {
        "name": "list_workflows",
        "group": "workflow",
        "description": "列出工作流及其节点规模、状态与最近一次运行",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "get_workflow",
        "group": "workflow",
        "description": "查单个工作流的完整定义：节点、连线、运行输入",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "run_workflow",
        "group": "workflow",
        "description": "按 ID 运行工作流，返回运行流水号与状态",
        # 会真实执行整张画布（调模型、查知识库、发请求），按需确认执行
        "risk": "approval_required",
        "mount": "always",
    },
    {
        "name": "list_workflow_runs",
        "group": "workflow",
        "description": "查工作流运行记录（状态、耗时、失败原因）",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "get_workflow_run",
        "group": "workflow",
        "description": "查单次运行的逐节点结果：每步输入、输出、耗时与报错",
        "risk": "safe",
        "mount": "always",
    },
]


async def _web_search_signature(
    # 参数名与默认值就是前端运行表单的字段定义，函数体不读它们也不能改名
    query: str,  # pyright: ignore[reportUnusedParameter]
    count: int = 5,  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """仅用于导出参数签名；真正执行走 run_builtin_tool。"""

    raise RuntimeError("web_search 须经 run_builtin_tool 注入 DeepSeek 凭证后执行")


# 目录、ADK 装配、技能声明、手动运行都从这一张表取实现，避免多处硬编码工具名
_BUILTIN_TOOL_REGISTRY: dict[str, Any] = {
    "query_knowledge": query_knowledge,
    "web_search": _web_search_signature,
    "list_workflows": list_workflows,
    "get_workflow": get_workflow,
    "run_workflow": run_workflow,
    "list_workflow_runs": list_workflow_runs,
    "get_workflow_run": get_workflow_run,
}


def builtin_tool_registry() -> dict[str, Any]:
    """返回内置工具注册表副本。"""

    return dict(_BUILTIN_TOOL_REGISTRY)


def tool_impl(name: str) -> Any:
    """取工具实现；未知名显式失败。"""

    impl = _BUILTIN_TOOL_REGISTRY.get(name)
    if impl is None:
        raise LookupError(f"未知内置工具: {name}")
    return impl


def workflow_tool_names() -> list[str]:
    """工作流工具组：ADK 装配只认这一份清单。"""

    return [spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["group"] == "workflow"]


def tool_param_specs(func: Any) -> list[dict]:
    """按函数签名导出参数定义，供目录 / 运行表单渲染。"""

    signature = inspect.signature(func)
    specs: list[dict] = []
    for name, param in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        spec: dict[str, Any] = {
            "name": name,
            "type": "string",
            "required": param.default is inspect.Parameter.empty,
        }
        if param.default is not inspect.Parameter.empty:
            spec["default"] = param.default
        # with future annotations 时注解是字符串，按名字判断
        annotation = param.annotation
        annotation_name = (
            annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
        )
        if annotation_name == "int":
            spec["type"] = "int"
        elif annotation_name == "float":
            spec["type"] = "float"
        elif annotation_name == "bool":
            spec["type"] = "bool"
        doc = inspect.getdoc(func)
        if doc is not None:
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{name}:"):
                    spec["desc"] = stripped[len(name) + 1 :].strip()
                    break
        specs.append(spec)
    return specs
