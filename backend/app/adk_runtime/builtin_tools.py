"""平台自带 Tool：只保留用户对话里真正需要的能力。"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any

from google.adk.tools import FunctionTool

from app.services.knowledge_scope import KnowledgeScope
from app.services.knowledge_service import search_for_agent

logger = logging.getLogger(__name__)


async def query_knowledge(query: str, limit: int = 5) -> dict:
    """语义检索组织知识库，返回标题、路径、摘录与相关度。

    结果按相关度从高到低排列，序号从 1 开始；回答时应以 [序号] 标注引用条目。

    Args:
        query: 检索语句
        limit: 返回条数上限
    """

    return await search_for_agent(query, limit)


# 侧边栏 /tools：平台能力目录（web_search 由对话开关按需挂载）
# mount 说明装配时机：always = 随 SkillToolset 始终装配；per_turn = 由对话开关决定
BUILTIN_TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "query_knowledge",
        "group": "builtin",
        "description": "语义检索组织知识库",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "web_search",
        "group": "builtin",
        "description": "DeepSeek 官方网页搜索（对话开启联网时可用）",
        "risk": "safe",
        "mount": "per_turn",
    },
]


def _scoped_query_knowledge(scope: KnowledgeScope):
    """把助理知识范围绑进 query_knowledge；工具名与文档保持不变。"""

    @functools.wraps(query_knowledge)
    async def scoped(query: str, limit: int = 5) -> dict:
        return await search_for_agent(query, limit, scope=scope)

    return scoped


def build_knowledge_tools(scope: KnowledgeScope | None = None) -> list[FunctionTool]:
    """供 SkillToolset.additional_tools 挂载；scope 为空时使用全库检索。"""

    if scope is None or scope.is_empty:
        return [FunctionTool(query_knowledge)]
    logger.info(
        "装配带知识范围的 query_knowledge paths=%s doc_ids=%s",
        list(scope.paths),
        len(scope.doc_ids),
    )
    return [FunctionTool(_scoped_query_knowledge(scope))]


def build_builtin_tools() -> list[FunctionTool]:
    """初始上下文不挂 Tool；由 Skill 激活后注入。"""

    return []


# Skill 声明的 additional_tools 必须能在此注册表解析；
# 不在表内则技能目录标为不可装配（inventory_only）。
_BUILTIN_TOOL_REGISTRY: dict[str, Any] = {
    "query_knowledge": query_knowledge,
}


def builtin_tool_registry() -> dict[str, Any]:
    """返回内置工具注册表副本。"""

    return dict(_BUILTIN_TOOL_REGISTRY)


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
        # 从 docstring Args 段提取单行说明（没有则省略）
        doc = inspect.getdoc(func)
        if doc is not None:
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{name}:"):
                    spec["desc"] = stripped[len(name) + 1 :].strip()
                    break
        specs.append(spec)
    return specs
