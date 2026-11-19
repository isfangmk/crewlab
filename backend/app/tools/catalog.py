"""内置工具的目录声明、注册表与参数导出。

这里只描述「有哪些工具、怎么分组、怎么展示参数」，
工具本身的行为在各主题模块里；ADK 装配在 adk_runtime 里。
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from app.tools.builtin_names import (
    BUILTIN_SELECTABLE_TOOL_NAMES as _SELECTABLE_NAMES,
)
from app.tools.code_interpreter import run_python
from app.tools.knowledge import query_knowledge
from app.tools.memory import (
    delete_memory,
    recall_memory,
    remember_memory,
    update_memory,
)
from app.tools.platform_api import call_platform_api, search_platform_api
from app.tools.task_graph import get_task_graph, submit_task_graph, update_task_graph
from app.tools.user_input import ASK_USER_TOOL_NAME, ask_user

# mount 说明装配时机：always = 随 SkillToolset 始终装配；per_turn = 由对话开关决定
BUILTIN_TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "run_python",
        "group": "code",
        "description": "在沙盒中执行 Python 代码；画图用 chart(data, 图型, 标题) 产出图表，emit_table/emit_file 产出表格与文件",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "remember_memory",
        "group": "memory",
        "description": "把值得长期记住的事实或偏好保存到跨会话记忆",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "recall_memory",
        "group": "memory",
        "description": "按语义回忆相关记忆（本助理记忆 ∪ 全局记忆）",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "update_memory",
        "group": "memory",
        "description": "改写一条已有记忆的正文（需先 recall_memory 拿 id）；会重新向量化",
        "risk": "mutating",
        "mount": "always",
    },
    {
        "name": "delete_memory",
        "group": "memory",
        "description": "删除一条记忆（需先 recall_memory 拿 id），不可恢复",
        "risk": "mutating",
        "mount": "always",
    },
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
        "description": "DeepSeek 官方网页搜索（对话开启联网时可用）；query 最长 128 字符",
        "risk": "safe",
        "mount": "per_turn",
    },
    {
        "name": ASK_USER_TOOL_NAME,
        "group": "interaction",
        "description": "向用户发起澄清：弹卡片让用户选择选项或填写信息，答案回流后继续",
        # 只在有人值守的对话里用：子 Agent 与工作流无人值守，不挂载该工具
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "submit_task_graph",
        "group": "taskgraph",
        "description": "提交一张任务图：节点按 depends_on 表达并行/串行/汇合拓扑；用户确认后按拓扑执行，收尾自动汇总进对话",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "get_task_graph",
        "group": "taskgraph",
        "description": "按图标识取执行进度快照：各节点状态、结论、证据与用量",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "update_task_graph",
        "group": "taskgraph",
        "description": "按用户意见调整任务图节点与依赖（整图替换）；仅草稿态可改",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "create_skill",
        "group": "config",
        "description": "按用户描述新建技能：需给出 key/description/instructions/risk/group，可通过 tools 声明技能工具",
        "risk": "mutating",
        "mount": "always",
    },
    {
        "name": "install_skill_from_url",
        "group": "config",
        "description": "从网址安装技能：支持 zip 直链或 git 仓库（github 等），下载后导入为平台技能",
        "risk": "mutating",
        "mount": "always",
    },
    {
        "name": "install_skill_from_zip",
        "group": "config",
        "description": "从对话上传的 zip 附件安装技能：参数是 attachment_id",
        "risk": "mutating",
        "mount": "always",
    },
    {
        "name": "search_platform_api",
        "group": "platform",
        "description": "按自然语言检索平台可操作的接口，返回候选 api_id 与参数说明",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "call_platform_api",
        "group": "platform",
        "description": "按 api_id 调用平台接口（写操作需人工审批）",
        "risk": "mutating",
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


async def _create_skill_signature(
    key: str,  # pyright: ignore[reportUnusedParameter]
    description: str,  # pyright: ignore[reportUnusedParameter]
    instructions: str,  # pyright: ignore[reportUnusedParameter]
    risk: str,  # pyright: ignore[reportUnusedParameter]
    group: str,  # pyright: ignore[reportUnusedParameter]
    preapproved_tools: str = "",  # pyright: ignore[reportUnusedParameter]
    tools: str = "",  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """仅用于导出参数签名；真正执行走对话装配的 FunctionTool。"""

    raise RuntimeError("create_skill 须经对话工具装配后执行")


async def _install_skill_from_url_signature(
    url: str,  # pyright: ignore[reportUnusedParameter]
    risk: str = "",  # pyright: ignore[reportUnusedParameter]
    group: str = "",  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """仅用于导出参数签名；真正执行走对话工具装配的 FunctionTool。"""

    raise RuntimeError("install_skill_from_url 须经对话工具装配后执行")


async def _install_skill_from_zip_signature(
    attachment_id: str,  # pyright: ignore[reportUnusedParameter]
    risk: str = "",  # pyright: ignore[reportUnusedParameter]
    group: str = "",  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """仅用于导出参数签名；真正执行走对话工具装配的 FunctionTool。"""

    raise RuntimeError("install_skill_from_zip 须经对话工具装配后执行")


# 目录、ADK 装配、技能声明、手动运行都从这一张表取实现，避免多处硬编码工具名
_BUILTIN_TOOL_REGISTRY: dict[str, Any] = {
    "run_python": run_python,
    "remember_memory": remember_memory,
    "recall_memory": recall_memory,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    "query_knowledge": query_knowledge,
    "web_search": _web_search_signature,
    ASK_USER_TOOL_NAME: ask_user,
    "submit_task_graph": submit_task_graph,
    "get_task_graph": get_task_graph,
    "update_task_graph": update_task_graph,
    "create_skill": _create_skill_signature,
    "install_skill_from_url": _install_skill_from_url_signature,
    "install_skill_from_zip": _install_skill_from_zip_signature,
    "search_platform_api": search_platform_api,
    "call_platform_api": call_platform_api,
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


def builtin_group_names(*groups: str) -> list[str]:
    """按分组取工具名（保持声明顺序）；ADK 装配与目录都用它。"""

    return [spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["group"] in groups]


def builtin_selectable_tool_names() -> list[str]:
    """可勾选的内置工具名（声明顺序）；单一数据源在 builtin_names。"""

    return list(_SELECTABLE_NAMES)


def all_builtin_tool_names() -> list[str]:
    """全部可勾选的内置工具名；新助理默认全选。"""

    return builtin_selectable_tool_names()


# 校验用集合，装配与前端勾选的名单都以 builtin_names 为准。
BUILTIN_SELECTABLE_TOOL_NAMES: frozenset[str] = frozenset(_SELECTABLE_NAMES)

# 名单必须与目录声明的常驻工具一致，防止两处漂移。
if frozenset(
    spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["mount"] == "always"
) != BUILTIN_SELECTABLE_TOOL_NAMES:
    raise RuntimeError("可勾选内置工具名单与目录声明不一致")


def tool_param_specs(func: Any) -> list[dict]:
    """按函数签名导出参数定义，供目录 / 运行表单渲染。

    tool_context 形参由 ADK 按注解注入，不进模型可见 schema，也不进运行表单。
    """

    signature = inspect.signature(func)
    specs: list[dict] = []
    for name, param in signature.parameters.items():
        if name in ("self", "cls", "tool_context"):
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


# 内置 Tool 的分组标题，供 /tools/builtin 目录接口使用。
BUILTIN_TOOL_GROUPS: dict[str, dict[str, str]] = {
    "code": {"title": "代码解释器", "title_en": "Code interpreter"},
    "knowledge": {"title": "知识检索", "title_en": "Knowledge"},
    "search": {"title": "联网搜索", "title_en": "Web search"},
    "memory": {"title": "记忆", "title_en": "Memory"},
    "interaction": {"title": "澄清交互", "title_en": "User input"},
    "taskgraph": {"title": "任务图执行", "title_en": "Task graph"},
    "config": {"title": "技能管理", "title_en": "Skill management"},
    "platform": {"title": "平台操作", "title_en": "Platform operations"},
}


# 字符串参数按声明类型解析：画布参数一律以文本录入，执行前必须转成工具签名要的类型。
_INT_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_TRUE_TEXT = {"true", "1", "yes"}
_FALSE_TEXT = {"false", "0", "no"}


def _coerce_value(tool: str, param: str, declared: str, value: Any) -> Any:
    """把单个参数转成声明类型；不是字符串时也按值的实际类型校验。"""

    if declared == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        raise ValueError(f"工具 {tool} 的参数 {param} 是文本类型，收到 {type(value).__name__}")

    if declared == "int":
        # bool 是 int 的子类，必须单独挡掉，否则 true 会变成 1
        if isinstance(value, bool):
            raise ValueError(f"工具 {tool} 的参数 {param} 需要整数，收到布尔值")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"工具 {tool} 的参数 {param} 需要整数，收到 {value}")
            return int(value)
        if isinstance(value, str) and _INT_PATTERN.fullmatch(value.strip()):
            return int(value.strip())
        raise ValueError(f"工具 {tool} 的参数 {param} 需要整数，收到 {value!r}")

    if declared == "float":
        if isinstance(value, bool):
            raise ValueError(f"工具 {tool} 的参数 {param} 需要数字，收到布尔值")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and _FLOAT_PATTERN.fullmatch(value.strip()):
            return float(value.strip())
        raise ValueError(f"工具 {tool} 的参数 {param} 需要数字，收到 {value!r}")

    if declared == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE_TEXT:
                return True
            if text in _FALSE_TEXT:
                return False
        raise ValueError(f"工具 {tool} 的参数 {param} 需要 true/false，收到 {value!r}")

    return value


def coerce_tool_params(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """校验参数名与必填项，并按工具签名把文本值转成声明类型。

    空字符串视为未填：交给签名默认值，避免把 number 参数的空格解析失败。
    """

    specs = tool_param_specs(tool_impl(name))
    allowed = {spec["name"] for spec in specs}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"工具 {name} 不接受参数: {','.join(unknown)}")

    kwargs: dict[str, Any] = {}
    for spec in specs:
        pname = spec["name"]
        missing = pname not in params or params[pname] is None or params[pname] == ""
        if missing:
            if spec.get("required"):
                raise ValueError(f"工具 {name} 缺少必填参数: {pname}")
            continue
        kwargs[pname] = _coerce_value(name, pname, spec["type"], params[pname])
    return kwargs
