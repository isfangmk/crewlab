"""平台自带 Tool：只保留用户对话里真正需要的能力。"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from google.adk.tools import FunctionTool

from app.services.knowledge_service import search_for_agent

logger = logging.getLogger(__name__)


async def query_knowledge(query: str, limit: int = 5) -> dict:
    """语义检索组织知识库，返回标题、路径、摘录与相关度。"""

    return await search_for_agent(query, limit)


# 侧边栏 /tools：平台能力目录（web_search 由对话开关按需挂载）
BUILTIN_TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "query_knowledge",
        "description": "语义检索组织知识库",
        "risk": "safe",
    },
    {
        "name": "web_search",
        "description": "DeepSeek 官方网页搜索（对话开启联网时可用）",
        "risk": "safe",
    },
]


def build_knowledge_tools() -> list[FunctionTool]:
    """供 SkillToolset.additional_tools 挂载。"""

    return [FunctionTool(query_knowledge)]


def build_builtin_tools() -> list[FunctionTool]:
    """初始上下文不挂 Tool；由 Skill 激活后注入。"""

    return []


# 技能页「执行」按名字直接调用技能声明的工具。
# 这里维护「工具名 → 可调用对象」的注册表；
# 技能声明的工具不在注册表内必须显式失败，不能静默跳过。
_BUILTIN_TOOL_REGISTRY: dict[str, Any] = {
    "query_knowledge": query_knowledge,
}


def builtin_tool_registry() -> dict[str, Any]:
    """返回内置工具注册表副本。"""

    return dict(_BUILTIN_TOOL_REGISTRY)


def tool_param_specs(func: Any) -> list[dict]:
    """按函数签名导出参数定义，供技能页渲染表单。"""

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
        specs.append(spec)
    return specs
