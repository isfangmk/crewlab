"""内置工具的目录声明、注册表与参数导出。

这里只描述「有哪些工具、怎么分组、怎么展示参数」，
工具本身的行为在各主题模块里；ADK 装配在 adk_runtime 里。
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from app.tools.code_interpreter import run_python
from app.tools.investigation import dispatch_investigation, get_investigation
from app.tools.knowledge import query_knowledge
from app.tools.memory import recall_memory, remember_memory
from app.tools.user_input import ASK_USER_TOOL_NAME, ask_user
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
        # 会真实执行整张画布（调模型、查知识库、发请求），定级为变更
        "risk": "mutating",
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
    {
        "name": "dispatch_investigation",
        "group": "investigation",
        "description": "把多线索问题派给 N 路只读子代理并行排查，返回带证据的结构化发现；软截止先出初步汇总，未完的路后台继续",
        "risk": "safe",
        "mount": "always",
    },
    {
        "name": "get_investigation",
        "group": "investigation",
        "description": "按排查标识取并行排查的最新快照：各路状态、发现与预算",
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
    "run_python": run_python,
    "remember_memory": remember_memory,
    "recall_memory": recall_memory,
    "query_knowledge": query_knowledge,
    "web_search": _web_search_signature,
    ASK_USER_TOOL_NAME: ask_user,
    "list_workflows": list_workflows,
    "get_workflow": get_workflow,
    "run_workflow": run_workflow,
    "list_workflow_runs": list_workflow_runs,
    "get_workflow_run": get_workflow_run,
    "dispatch_investigation": dispatch_investigation,
    "get_investigation": get_investigation,
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


def builtin_group_names(*groups: str) -> list[str]:
    """按分组取工具名（保持声明顺序）；ADK 装配与目录都用它。"""

    return [spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["group"] in groups]


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


# 内置 Tool 目录与画布工具节点共用同一份分组标题
BUILTIN_TOOL_GROUPS: dict[str, dict[str, str]] = {
    "code": {"title": "代码解释器", "title_en": "Code interpreter"},
    "knowledge": {"title": "知识检索", "title_en": "Knowledge"},
    "search": {"title": "联网搜索", "title_en": "Web search"},
    "memory": {"title": "记忆", "title_en": "Memory"},
    "workflow": {"title": "工作流", "title_en": "Workflow"},
    "interaction": {"title": "澄清交互", "title_en": "User input"},
    "investigation": {"title": "并行排查", "title_en": "Investigation"},
}


# 画布「工具」节点只暴露没有专用节点、且能在无人值守运行中安全执行的内置工具：
# 知识检索 / 联网搜索 / 代码已有专用节点；记忆类依赖会话上下文；
# run_workflow 需要人工确认，都不能放进画布。
WORKFLOW_NODE_TOOL_NAMES = frozenset(
    {
        "list_workflows",
        "get_workflow",
        "list_workflow_runs",
        "get_workflow_run",
    }
)


def workflow_builtin_tools() -> list[dict[str, Any]]:
    """画布「工具」节点可选的内置工具（含参数定义）。

    只给没有专用节点、且无人值守执行安全的工作流查询工具；其余能力
    在画布上有独立节点，重复暴露只会让编排时不知道该选哪个。
    """

    tools: list[dict[str, Any]] = []
    for spec in BUILTIN_TOOL_SPECS:
        if spec["name"] not in WORKFLOW_NODE_TOOL_NAMES:
            continue
        group = BUILTIN_TOOL_GROUPS.get(spec["group"])
        if group is None:
            raise RuntimeError(f"内置工具分组缺少标题: {spec['group']}")
        tools.append(
            {
                "name": spec["name"],
                "group": spec["group"],
                "group_title": group["title"],
                "group_title_en": group["title_en"],
                "description": spec["description"],
                "risk": spec["risk"],
                "params": tool_param_specs(tool_impl(spec["name"])),
            }
        )
    return tools


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
