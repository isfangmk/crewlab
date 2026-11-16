"""worker 运行器：单路子代理的只读装配、headless 执行与 findings 收集。

口径：
  - worker 复用子助理装配（build_chat_agent），但内置工具只挂只读白名单；
  - MCP / 技能工具按名称启发式禁写（用户已知启发式有漏网风险），
    闸门只拦截并返回错误，绝不进审批确认——无人值守不能悬挂；
  - 执行走 InMemoryRunner headless 范式（同 workflow_node_impl 的模型分支），
    预算三重：步数（RunConfig.max_llm_calls）+ 墙钟 + token 软限；
  - findings 证据必须落在本路实收 call_id 集合内，防模型编造。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.adk.tools import FunctionTool
from google.genai import types

from app.adk_runtime.agent_factory import build_chat_agent
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

# findings 输出协议：worker 回答末尾的围栏 JSON
_FINDINGS_FENCE_RE = re.compile(r"```findings\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class WorkerBudget:
    """单路 worker 的三重预算。"""

    # RunConfig.max_llm_calls；0 表示不限步
    max_steps: int = 8
    # 墙钟秒数，由 run_worker 自行 wait_for 包线
    timeout_seconds: int = 120
    # token 软限：累计 usage 超限即中止该路；0 表示不限
    token_budget: int = 8000


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


class _BudgetExceeded(Exception):
    """token 软限超限：内部信号，转成 failed 结果。"""


def build_worker_tool_gate(
    disallowed_tool_names: set[str] | None = None,
) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    """worker 专用工具闸门：命中禁写启发式或禁用名单即拒绝，永不进审批确认。"""

    denied = set(disallowed_tool_names or ())

    async def before_tool_callback(tool: Any, args: Any, tool_context: Any) -> dict[str, Any] | None:
        name = getattr(tool, "name", "") or ""
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
    question: str,
    extra_tools: list[Any] | None = None,
    skills: list[Any] | None = None,
    knowledge_scope: KnowledgeScope | None = None,
    model_prefix: str = "",
    provider_extra_body: dict[str, Any] | None = None,
    disallowed_tool_names: set[str] | None = None,
    budget: WorkerBudget | None = None,
    llm_timeout_seconds: int = 120,
) -> LlmAgent:
    """装配单路 worker：只读内置白名单 + 知识检索 + 继承的 MCP（禁写闸门拦）。"""

    budget = budget or WorkerBudget()
    tools: list[Any] = [FunctionTool(tool_impl(n)) for n in WORKER_READONLY_TOOL_NAMES]
    tools.extend(build_knowledge_tools(knowledge_scope))
    if extra_tools:
        tools.extend(extra_tools)
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
    return build_chat_agent(
        provider_id=provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        agent_name=f"worker-{name}",
        agent_description=f"并行排查 worker：{target}",
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
        before_tool_callback=build_worker_tool_gate(disallowed_tool_names),
    )


def parse_findings(text: str, call_ids: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """解析 worker 的 findings 围栏；解析失败兜底为纯文本结论。

    证据 call_id 必须在实收集合内，编造的证据直接丢弃。
    """

    matches = _FINDINGS_FENCE_RE.findall(text)
    raw = matches[-1].strip() if matches else ""
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            conclusion = str(data.get("conclusion") or "").strip()
            findings: list[dict[str, Any]] = []
            for item in data.get("findings") or []:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                detail = str(item.get("detail") or "").strip()
                if title == "" and detail == "":
                    continue
                evidence: list[dict[str, str]] = []
                for ev in item.get("evidence") or []:
                    if not isinstance(ev, dict):
                        continue
                    cid = str(ev.get("call_id") or "").strip()
                    if cid not in call_ids:
                        continue
                    evidence.append(
                        {
                            "call_id": cid,
                            "tool": str(ev.get("tool") or "").strip(),
                            "excerpt": str(ev.get("excerpt") or "").strip(),
                        }
                    )
                findings.append({"title": title, "detail": detail, "evidence": evidence})
            return conclusion or text.strip(), findings
    return text.strip(), []


def _usage_from_event(event: Any) -> tuple[int | None, int | None, int | None] | None:
    """读本事件 usage；无有效 token 字段则返回 None（禁止假 0）。"""

    meta = getattr(event, "usage_metadata", None)
    if meta is None:
        return None
    prompt = getattr(meta, "prompt_token_count", None)
    completion = getattr(meta, "candidates_token_count", None)
    total = getattr(meta, "total_token_count", None)
    if not isinstance(prompt, int) and not isinstance(completion, int) and not isinstance(total, int):
        return None
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
        total if isinstance(total, int) else None,
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
    started = time.monotonic()
    log_lines: list[str] = []

    async def _log(line: str, tool: str = "") -> None:
        log_lines.append(line)
        await publish(
            {
                "event": "worker_log",
                "dispatch_uid": dispatch_uid,
                "worker_uid": worker_uid,
                "line": line,
                "tool": tool,
            }
        )

    def _finish(status: str, *, conclusion: str = "", findings: list[dict[str, Any]] | None = None,
                error: str = "", usage: dict[str, int] | None = None) -> WorkerResult:
        return WorkerResult(
            worker_uid=worker_uid,
            status=status,
            conclusion=conclusion,
            findings=findings or [],
            error=error,
            usage=usage or {},
            duration_ms=int((time.monotonic() - started) * 1000),
            log_text="\n".join(log_lines),
        )

    await _log(f"开始排查：{name} / {target}")

    runner = InMemoryRunner(agent=agent, app_name=WORKER_APP_NAME)
    try:
        try:
            result = await asyncio.wait_for(
                _execute(
                    runner=runner,
                    prompt=prompt,
                    budget=budget,
                    publish=publish,
                    dispatch_uid=dispatch_uid,
                    worker_uid=worker_uid,
                    log=_log,
                    stop_check=stop_check,
                ),
                timeout=budget.timeout_seconds if budget.timeout_seconds > 0 else None,
            )
        except asyncio.TimeoutError:
            await _log(f"超时中止（{budget.timeout_seconds}s）")
            return _finish("failed", error=f"执行超时（{budget.timeout_seconds}s）")
        except _BudgetExceeded as exc:
            await _log(str(exc))
            return _finish("failed", error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单路失败不拖垮整次排查
            logger.exception("worker 执行异常 worker_uid=%s", worker_uid)
            await _log(f"执行异常：{exc}")
            return _finish("failed", error=str(exc))

        if result["stopped"]:
            await _log("收到停止信号，本路中止")
            return _finish(
                "stopped",
                conclusion=result["conclusion"],
                findings=result["findings"],
                usage=result["usage"],
            )
        await _log("排查完成")
        return _finish(
            "succeeded",
            conclusion=result["conclusion"],
            findings=result["findings"],
            usage=result["usage"],
        )
    finally:
        # Runner.close() 是唯一关闭 MCP toolset 的路径，必须无条件执行
        try:
            await runner.close()
        except Exception:  # noqa: BLE001
            logger.warning("worker runner.close() 失败 worker_uid=%s", worker_uid)


async def _execute(
    *,
    runner: InMemoryRunner,
    prompt: str,
    budget: WorkerBudget,
    publish: Callable[[dict[str, Any]], Awaitable[None]],
    dispatch_uid: str,
    worker_uid: str,
    log: Callable[[str, str], Awaitable[None]],
    stop_check: Callable[[], bool],
) -> dict[str, Any]:
    """headless 跑一个 worker 回合，收集文本 / call_id / usage。"""

    session = await runner.session_service.create_session(
        app_name=WORKER_APP_NAME, user_id=WORKER_APP_NAME
    )
    run_config = RunConfig()
    if budget.max_steps > 0:
        run_config.max_llm_calls = budget.max_steps

    answer_parts: list[str] = []
    call_ids: set[str] = set()
    usage_prompt: int | None = None
    usage_completion: int | None = None
    usage_total: int | None = None
    stopped = False

    async for event in runner.run_async(
        user_id=WORKER_APP_NAME,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        run_config=run_config,
    ):
        if stop_check():
            stopped = True
            break
        piece = _usage_from_event(event)
        if piece is not None:
            prompt_n, completion_n, total_n = piece
            if prompt_n is not None:
                usage_prompt = (usage_prompt or 0) + prompt_n
            if completion_n is not None:
                usage_completion = (usage_completion or 0) + completion_n
            if total_n is not None:
                usage_total = (usage_total or 0) + total_n
            # token 软限：累计消耗超预算即中止该路
            if budget.token_budget > 0 and (usage_total or 0) > budget.token_budget:
                raise _BudgetExceeded(f"预算超限：已用 {usage_total} tokens（每路 ≤ {budget.token_budget}）")
        content = getattr(event, "content", None)
        if content is None or getattr(content, "parts", None) is None:
            continue
        for part in content.parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                cid = getattr(fc, "id", "") or ""
                fname = getattr(fc, "name", "") or ""
                if isinstance(cid, str) and cid != "":
                    call_ids.add(cid)
                await log(f"调用工具 {fname}", tool=fname)
                continue
            fr = getattr(part, "function_response", None)
            if fr is not None:
                fname = getattr(fr, "name", "") or ""
                await log(f"工具返回 {fname}", tool=fname)
                continue
            if part.text:
                answer_parts.append(part.text)

    answer = "".join(answer_parts)
    conclusion, findings = parse_findings(answer, call_ids)
    usage: dict[str, int] = {}
    if usage_prompt is not None:
        usage["prompt_tokens"] = usage_prompt
    if usage_completion is not None:
        usage["completion_tokens"] = usage_completion
    if usage_total is not None:
        usage["total_tokens"] = usage_total
    return {
        "stopped": stopped,
        "conclusion": conclusion,
        "findings": findings,
        "usage": usage,
        "call_ids": call_ids,
    }
