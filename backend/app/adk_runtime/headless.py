"""无人值守回合执行引擎：InMemoryRunner headless 范式 + 三重预算 + 结构化输出。

排查 worker 与计划步骤共用这一个执行循环：预算三重（步数/墙钟/token 软限），
超时/超限/停止/异常全部转成结构化结果，绝不让异常打断整个回合。
工具异常已在 agent 装配层兜底成错误结果（on_tool_error_callback），
模型可自行修正重试；引擎只兜底回合级故障。
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
from google.adk.agents._streaming_mode import StreamingMode
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types

logger = logging.getLogger(__name__)

# findings 输出协议：回合回答末尾的围栏 JSON
_FINDINGS_FENCE_RE = re.compile(r"```findings\s*\n(.*?)```", re.DOTALL)

# 无人值守临时会话 state 键：工具借此回传真实会话 id（产出物归属用）
HEADLESS_SESSION_ID_KEY = "ongrid_real_session_id"

# 工具轨迹里单个入参/返回的收敛上限：结构化数据超限则转截断文本，防 token 爆
_TRACE_VALUE_LIMIT = 12000


def _trace_value(value: Any) -> Any:
    """工具入参/返回写入轨迹前的体量收敛：短结构原样保留，超限转截断文本。"""

    if value is None:
        return None
    if isinstance(value, str):
        return value[:_TRACE_VALUE_LIMIT]
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)[:_TRACE_VALUE_LIMIT]
    if len(text) <= _TRACE_VALUE_LIMIT:
        return value
    return text[:_TRACE_VALUE_LIMIT] + "…(truncated)"


@dataclass(frozen=True)
class WorkerBudget:
    """单个无人值守回合的三重预算（缺省与派发/计划工具默认一致，单一源头）。"""

    # RunConfig.max_llm_calls；0 表示不限步
    max_steps: int = 12
    # 墙钟秒数，超时中止该回合；0 表示不限
    timeout_seconds: int = 300
    # token 软限：累计 usage 超限即中止；0 表示不限
    token_budget: int = 150000


@dataclass
class HeadlessResult:
    """一个无人值守回合的执行结果。"""

    # succeeded | failed | stopped
    status: str
    conclusion: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    # 有值才写键，缺键不造假 0
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    log_text: str = ""
    # 本回合工具调用轨迹：{call_id, name, args, response}，供跨步上下文与汇总复用
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


class BudgetExceeded(Exception):
    """token 软限超限：内部信号，转成 failed 结果。"""


def parse_findings(text: str, call_ids: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """解析回答末尾的 findings 围栏；解析失败兜底为纯文本结论。

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


async def run_headless_turn(
    *,
    agent: LlmAgent,
    app_name: str,
    prompt: str,
    budget: WorkerBudget,
    log: Callable[..., Awaitable[None]],
    stop_check: Callable[[], bool],
    opening: str,
    closing: str,
    session_state: dict[str, Any] | None = None,
    on_tool_response: Callable[[str, str, Any], Awaitable[None]] | None = None,
    on_text_delta: Callable[[str, bool], Awaitable[None]] | None = None,
) -> HeadlessResult:
    """跑完一个无人值守回合；超时/超限/停止/异常都转成结构化结果。

    log 按 (line, tool=...) 调用；opening/closing 是首尾日志行，
    由调用方按各自语义给出（如「开始排查：… / 排查完成」）。
    session_state 注入临时会话状态：无人值守 agent 的 tool_context.session
    是独立临时会话，工具（如 run_python）借它回传真实会话等上下文。
    on_tool_response 在收到工具返回时按 (call_id, name, response) 触发，
    供调用方落产出物等副作用；不传则无额外行为。
    on_text_delta 传入即开启 SSE 流式模式，按 (delta, partial) 逐段推文本；
    不传则保持非流式的整段收集（排查/step 默认口径不变）。
    """

    started = time.monotonic()
    log_lines: list[str] = []
    # 失败中断时也要报已耗用量：回合内实时同步进来，_finish 失败路径原样带回
    usage_partial: dict[str, int] = {}
    # 工具轨迹同理实时同步：中途失败也能带回已收集的工具输入输出
    tool_trace_partial: list[dict[str, Any]] = []

    async def _log(line: str, tool: str = "") -> None:
        log_lines.append(line)
        await log(line, tool=tool)

    def _finish(status: str, *, conclusion: str = "", findings: list[dict[str, Any]] | None = None,
                error: str = "", usage: dict[str, int] | None = None) -> HeadlessResult:
        return HeadlessResult(
            status=status,
            conclusion=conclusion,
            findings=findings or [],
            error=error,
            usage=usage or {},
            duration_ms=int((time.monotonic() - started) * 1000),
            log_text="\n".join(log_lines),
            tool_trace=list(tool_trace_partial),
        )

    await _log(opening)

    runner = InMemoryRunner(agent=agent, app_name=app_name)
    try:
        try:
            result = await asyncio.wait_for(
                _run_turn(
                    runner=runner,
                    prompt=prompt,
                    budget=budget,
                    log=_log,
                    stop_check=stop_check,
                    usage_partial=usage_partial,
                    tool_trace_partial=tool_trace_partial,
                    session_state=session_state,
                    on_tool_response=on_tool_response,
                    on_text_delta=on_text_delta,
                ),
                timeout=budget.timeout_seconds if budget.timeout_seconds > 0 else None,
            )
        except asyncio.TimeoutError:
            await _log(f"超时中止（{budget.timeout_seconds}s）")
            return _finish(
                "failed",
                error=f"执行超时（{budget.timeout_seconds}s）",
                usage=dict(usage_partial),
            )
        except BudgetExceeded as exc:
            await _log(str(exc))
            return _finish("failed", error=str(exc), usage=dict(usage_partial))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单回合失败不拖垮外层编排
            logger.exception("无人值守回合执行异常 app=%s", app_name)
            await _log(f"执行异常：{exc}")
            return _finish("failed", error=str(exc), usage=dict(usage_partial))

        if result["stopped"]:
            await _log("收到停止信号，中止执行")
            return _finish(
                "stopped",
                conclusion=result["conclusion"],
                findings=result["findings"],
                usage=result["usage"],
            )
        await _log(closing)
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
            logger.warning("runner.close() 失败 app=%s", app_name)


async def _run_turn(
    *,
    runner: InMemoryRunner,
    prompt: str,
    budget: WorkerBudget,
    # log 按 (line, tool=...) 调用；可选关键字参数无法用普通 Callable 精确表达
    log: Callable[..., Awaitable[None]],
    stop_check: Callable[[], bool],
    usage_partial: dict[str, int],
    tool_trace_partial: list[dict[str, Any]],
    session_state: dict[str, Any] | None = None,
    on_tool_response: Callable[[str, str, Any], Awaitable[None]] | None = None,
    on_text_delta: Callable[[str, bool], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """headless 跑一个回合，收集文本 / call_id / usage / 工具轨迹。

    usage_partial 实时同步已耗用量：回合中途失败（超限/超时）时，
    调用方仍能报出已消耗的 tokens。
    tool_trace_partial 实时同步工具轨迹：先记 call 的入参，response 到达时补齐。
    on_tool_response 在返回到达时触发，供调用方落产出物等副作用。
    on_text_delta 传入即开 SSE 流式：逐段推 (delta, partial)，并跳过终包回放。
    """

    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=runner.app_name, state=session_state
    )
    run_config = RunConfig()
    if on_text_delta is not None:
        # 流式汇总：开 SSE 模式拿逐段增量；普通 worker/step 保持非流式整段收集
        run_config.streaming_mode = StreamingMode.SSE
    if budget.max_steps > 0:
        run_config.max_llm_calls = budget.max_steps

    answer_parts: list[str] = []
    call_ids: set[str] = set()
    # call_id -> 轨迹条目占位；function_call 记入参，function_response 回填 response
    trace_by_call: dict[str, dict[str, Any]] = {}
    # 流式去重：推过 partial 后跳过 ADK 终包对整段的回放
    final_partial_seen = False
    text_delta_count = 0
    replay_skipped = 0
    usage_prompt: int | None = None
    usage_completion: int | None = None
    usage_total: int | None = None
    stopped = False

    async for event in runner.run_async(
        user_id=runner.app_name,
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
            # 先同步用量再看预算：超限中断也要留下已消耗的部分
            if usage_prompt is not None:
                usage_partial["prompt_tokens"] = usage_prompt
            if usage_completion is not None:
                usage_partial["completion_tokens"] = usage_completion
            if usage_total is not None:
                usage_partial["total_tokens"] = usage_total
            # token 软限：累计消耗超预算即中止该回合
            if budget.token_budget > 0 and (usage_total or 0) > budget.token_budget:
                raise BudgetExceeded(f"预算超限：已用 {usage_total} tokens（上限 {budget.token_budget}）")
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
                    # 入参先登记；response 未到也保留（失败轨迹同样可追溯）
                    entry: dict[str, Any] = {
                        "call_id": cid,
                        "name": fname,
                        "args": _trace_value(getattr(fc, "args", None)),
                        "response": None,
                    }
                    trace_by_call[cid] = entry
                    tool_trace_partial.append(entry)
                await log(f"调用工具 {fname}", tool=fname)
                continue
            fr = getattr(part, "function_response", None)
            if fr is not None:
                fname = getattr(fr, "name", "") or ""
                cid = getattr(fr, "id", "") or ""
                response = getattr(fr, "response", None)
                response_entry = trace_by_call.get(cid) if isinstance(cid, str) else None
                if response_entry is not None:
                    response_entry["response"] = _trace_value(response)
                await log(f"工具返回 {fname}", tool=fname)
                if on_tool_response is not None:
                    await on_tool_response(cid, fname, response)
                continue
            if part.text:
                # thought=True 是模型推理（DeepSeek reasoning_content），不进正文；
                # 与普通对话 stream_bridge 同口径，避免思考过程泄漏到回答/汇总。
                if getattr(part, "thought", False) is True:
                    continue
                text = part.text
                is_partial = bool(getattr(event, "partial", False)) and on_text_delta is not None
                # 流式终包会回放整段：推过 partial 后跳过，避免正文翻倍
                if not is_partial and final_partial_seen and on_text_delta is not None:
                    replay_skipped += 1
                    continue
                if is_partial:
                    final_partial_seen = True
                answer_parts.append(text)
                if on_text_delta is not None and text:
                    text_delta_count += 1
                    await on_text_delta(text, is_partial)

    answer = "".join(answer_parts)
    conclusion, findings = parse_findings(answer, call_ids)
    logger.info(
        "无人值守回合收集完成 app=%s stream=%s text_deltas=%s replay_skipped=%s "
        "text_len=%s tools=%s stopped=%s total_tokens=%s",
        runner.app_name,
        on_text_delta is not None,
        text_delta_count,
        replay_skipped,
        len(answer),
        len(call_ids),
        stopped,
        usage_total,
    )
    return {
        "stopped": stopped,
        "conclusion": conclusion,
        "findings": findings,
        "usage": dict(usage_partial),
        "call_ids": call_ids,
    }
