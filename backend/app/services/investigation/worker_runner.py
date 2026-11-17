"""worker 运行器：单路子代理的只读装配、headless 执行与 findings 收集。

口径：
  - worker 复用子助理装配（build_chat_agent），但内置工具只挂只读白名单；
  - MCP / 技能工具按名称启发式禁写（用户已知启发式有漏网风险），
    闸门只拦截并返回错误，绝不进审批确认——无人值守不能悬挂；
  - 执行走共用的无人值守回合引擎（adk_runtime.headless）：预算三重，
    超时/超限/停止/异常全转结构化结果；
  - findings 证据必须落在本路实收 call_id 集合内，防模型编造。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from app.adk_runtime.agent_factory import build_chat_agent
from app.adk_runtime.headless import WorkerBudget, run_headless_turn
from app.adk_runtime.toolset import build_knowledge_tools
from app.services.knowledge.knowledge_scope import KnowledgeScope
from app.tools.catalog import tool_impl

logger = logging.getLogger(__name__)

# headless 会话标识：worker 不进对话会话，用独立应用名隔离
WORKER_APP_NAME = "investigation_worker"

# worker 只读内置工具白名单：只查不改。
# query_knowledge 不在此列：知识检索统一由 build_knowledge_tools 按知识范围挂载，
# 两处同名注册会让模型侧校验「Tool names must be unique」直接拒单。
WORKER_READONLY_TOOL_NAMES: tuple[str, ...] = (
    "recall_memory",
    "list_workflows",
    "get_workflow",
    "list_workflow_runs",
    "get_workflow_run",
)

# MCP / 技能工具名禁写启发式：前缀动词或尾段动词即拒。
# 只按命名判断（MCP 无只读标记），已知有漏网风险，写操作必须只出现在主代理层。
_WORKER_WRITE_NAME_RE = re.compile(
    r"(?:^|__)(?:create|update|delete|remove|insert|drop|send|write|apply|execute"
    r"|push|restart|kill|cancel|edit|modify|upload|trigger|start|stop|run|invoke"
    r"|set|put|patch|post|add)(?:_|$)"
    r"|_(?:create|update|delete|remove|add|apply|send|write)$",
    re.IGNORECASE,
)

# 无人值守执行的统筹纪律：worker 与计划步骤共用，在 build_worker_agent 装配点统一注入。
# 工具面已按任务裁剪，但挂载不代表都要用——不加纪律时目标松散的单元会乱逛无关工具。
HEADLESS_FOCUS_GUIDANCE = (
    "聚焦纪律：只调用与本任务直接相关的工具，已挂载的工具不代表都要用一遍；"
    "与本任务无关的工具一律不调用，禁止为了「看看有没有」而扫描探索；"
    "证据不足时在结论里标注缺口，不要为了凑证据扩大调用范围。"
)


@dataclass
class WorkerResult:
    """单路 worker 的执行结果。"""

    worker_uid: str
    # succeeded | failed | stopped
    status: str
    conclusion: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    # 有值才写键，缺键不造假 0
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    log_text: str = ""


class ToolFocus(str, Enum):
    """工具挂载面的显式表达：NONE 表示不挂任何工具（区别于未声明的全量）。"""

    NONE = "__none__"


def normalize_tool_focus(
    focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | None,
) -> ToolFocus | frozenset[str] | None:
    """把上层声明归一成挂载口径：含 NONE 哨兵 → NONE；空 → None（全量）。"""

    if focus is ToolFocus.NONE:
        return ToolFocus.NONE
    if not focus:
        return None
    if ToolFocus.NONE.value in focus:
        return ToolFocus.NONE
    return frozenset(str(name) for name in focus)


def filter_tools_by_focus(
    tools: list[Any], focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | frozenset[str] | None
) -> list[Any]:
    """按声明收窄挂载面；NONE 不挂任何工具，空/None 保持全量。

    知识检索闭包版的函数名是 scoped_query_knowledge，按 query_knowledge 匹配。
    """

    if focus is ToolFocus.NONE:
        return []
    if not focus:
        return tools
    wanted = set(focus)
    kept: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        if name == "scoped_query_knowledge":
            name = "query_knowledge"
        if name in wanted:
            kept.append(tool)
    return kept


def build_worker_tool_gate(
    disallowed_tool_names: set[str] | None = None,
    allow: set[str] | None = None,
) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    """worker 专用工具闸门：命中禁写启发式或禁用名单即拒绝，永不进审批确认。

    allow 是免启发式白名单（如计划步骤的 run_python）：内置只读白名单是权威，
    启发式只拦 MCP / 技能这类没有只读标记的外部工具。
    """

    denied = set(disallowed_tool_names or ())
    permitted = set(allow or ())

    # args / tool_context 是 ADK before_tool_callback 契约参数（SDK 调用），实现侧不需要
    async def before_tool_callback(tool: Any, args: Any, tool_context: Any) -> dict[str, Any] | None:  # pyright: ignore[reportUnusedParameter]
        name = getattr(tool, "name", "") or ""
        if name in permitted:
            return None
        if name in denied or _WORKER_WRITE_NAME_RE.search(name):
            logger.info("worker 只读闸门拒绝工具 name=%s", name)
            return {"error": f"worker 只读排查：工具 {name} 被拒绝，仅允许只读查询"}
        return None

    return before_tool_callback


def build_worker_agent(
    *,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    name: str,
    target: str,
    instruction: str,
    extra_tools: list[Any] | None = None,
    skills: list[Any] | None = None,
    knowledge_scope: KnowledgeScope | None = None,
    model_prefix: str = "",
    provider_extra_body: dict[str, Any] | None = None,
    disallowed_tool_names: set[str] | None = None,
    budget: WorkerBudget | None = None,
    llm_timeout_seconds: int = 120,
    persona: str | None = None,
    readonly_tools: tuple[str, ...] = WORKER_READONLY_TOOL_NAMES,
    tool_focus: set[str] | tuple[str, ...] | list[str] | ToolFocus | None = None,
) -> LlmAgent:
    """装配单路无人值守 agent：只读内置白名单 + 知识检索 + 继承的 MCP（禁写闸门拦）。

    persona 覆盖默认排查人设（计划步骤传执行人设）；
    readonly_tools 覆盖只读内置白名单（计划步骤加 run_python）；
    tool_focus 是编排者声明的本路/本步所需工具，非空则按名收窄挂载面。
    """

    budget = budget or WorkerBudget()
    tools: list[Any] = [FunctionTool(tool_impl(n)) for n in readonly_tools]
    tools.extend(build_knowledge_tools(knowledge_scope))
    if extra_tools:
        tools.extend(extra_tools)
    tools = filter_tools_by_focus(tools, normalize_tool_focus(tool_focus))
    if persona is None:
        persona = (
            f"你是并行排查中的「{name}」，无人值守的只读排查员。\n"
            f"只使用只读查询工具收集证据，禁止任何写操作；工具被拒绝时换其他线索继续。\n"
            f"每条发现必须给出证据：引用工具调用的 call_id 与关键字段摘录。\n"
            f"完成时在回答最后输出一个围栏代码块（不要省略）：\n"
            f"```findings\n"
            f'{{"conclusion": "一句话结论", "findings": [{{"title": "发现标题", "detail": "细节", '
            f'"evidence": [{{"call_id": "工具调用id", "tool": "工具名", "excerpt": "关键字段摘录"}}]}}]}}\n'
            f"```"
        )
    if instruction.strip():
        persona = f"{persona}\n\n本路补充要求：{instruction.strip()}"
    # 统筹纪律最后压阵：worker 与计划步骤同一装配入口，一处注入全覆盖
    persona = f"{persona}\n{HEADLESS_FOCUS_GUIDANCE}"
    return build_chat_agent(
        provider_id=provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        agent_name=f"worker-{name}",
        agent_description=f"无人值守执行：{target}",
        persona_instruction=persona,
        thinking_enabled=False,
        model_prefix=model_prefix,
        provider_extra_body=provider_extra_body,
        extra_tools=tools,
        llm_timeout_seconds=llm_timeout_seconds,
        confirm_tool_names=set(),
        disallowed_tool_names=disallowed_tool_names,
        max_turns=budget.max_steps,
        knowledge_scope=knowledge_scope,
        skills=skills,
        include_builtin_tools=False,
        include_ask_user=False,
        include_followups=False,
        before_tool_callback=build_worker_tool_gate(
            disallowed_tool_names, allow=set(readonly_tools)
        ),
    )


async def run_worker(
    *,
    agent: LlmAgent,
    dispatch_uid: str,
    worker_uid: str,
    name: str,
    target: str,
    question: str,
    instruction: str,
    budget: WorkerBudget,
    publish: Callable[[dict[str, Any]], Awaitable[None]],
    stop_check: Callable[[], bool],
) -> WorkerResult:
    """跑完一路 worker 并收集结果；超时/超限/停止都转成结构化结果。"""

    prompt = (
        f"排查目标：{target}\n"
        f"总问题：{question}\n"
        + (f"补充要求：{instruction}\n" if instruction.strip() else "")
    )

    async def _log(line: str, tool: str = "") -> None:
        await publish(
            {
                "event": "worker_log",
                "dispatch_uid": dispatch_uid,
                "worker_uid": worker_uid,
                "line": line,
                "tool": tool,
            }
        )

    turn = await run_headless_turn(
        agent=agent,
        app_name=WORKER_APP_NAME,
        prompt=prompt,
        budget=budget,
        log=_log,
        stop_check=stop_check,
        opening=f"开始排查：{name} / {target}",
        closing="排查完成",
    )
    return WorkerResult(
        worker_uid=worker_uid,
        status=turn.status,
        conclusion=turn.conclusion,
        findings=turn.findings,
        error=turn.error,
        usage=turn.usage,
        duration_ms=turn.duration_ms,
        log_text=turn.log_text,
    )
