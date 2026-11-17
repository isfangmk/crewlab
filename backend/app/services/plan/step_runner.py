"""计划步骤执行：单步无人值守 agent 的人设、上下文与执行。

步骤复用 worker 装配（只读工具 + 知识检索 + 继承 MCP + 禁写闸门），
只换人设与只读白名单（计划步骤允许 run_python 做汇总分析）；
执行走共用的无人值守回合引擎（adk_runtime.headless）。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent

from app.adk_runtime.headless import (
    HEADLESS_SESSION_ID_KEY,
    HeadlessResult,
    WorkerBudget,
    run_headless_turn,
)
from app.services.investigation.worker_runner import WORKER_READONLY_TOOL_NAMES

# headless 会话标识：计划步骤不进对话会话，用独立应用名隔离
PLAN_APP_NAME = "plan_step"

# 计划步骤只读白名单：worker 白名单 + run_python（汇总矩阵/图表属分析产出）
PLAN_STEP_READONLY_TOOL_NAMES: tuple[str, ...] = WORKER_READONLY_TOOL_NAMES + (
    "run_python",
)


def build_plan_step_persona(name: str, instruction: str) -> str:
    """计划步骤的人设：无人值守单步执行，结构化输出结论与要点。"""

    persona = (
        f"你是执行计划中的「{name}」，无人值守的单步执行。\n"
        f"只使用只读查询工具收集证据，禁止任何写操作；工具被拒绝或报错时按原因换其他线索继续。\n"
        f"前置步骤已收集的数据、要点与工具结果都在上下文中，直接引用其中的结论和证据，"
        f"不要为获取相同数据重复调用工具。\n"
        f"每条要点必须给出证据：引用工具调用的 call_id 与关键字段摘录。\n"
        f"完成时在回答最后输出一个围栏代码块（不要省略）：\n"
        f"```findings\n"
        f'{{"conclusion": "本步结论摘要（1-2 句）", "findings": [{{"title": "要点标题", "detail": "细节", '
        f'"evidence": [{{"call_id": "工具调用id", "tool": "工具名", "excerpt": "关键字段摘录"}}]}}]}}\n'
        f"```"
    )
    if instruction.strip():
        persona = f"{persona}\n\n本步要求：{instruction.strip()}"
    return persona


def _compact(value: Any, limit: int = 800) -> str:
    """证据/工具值转单行文本，超限截断——前置上下文不当 token 黑洞。"""

    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text


def render_prior_context(items: list[dict[str, Any]]) -> list[str]:
    """把前置步骤的 conclusion/findings/tool_trace 渲染成可引用文本行。

    计划步骤与最终汇总共用同一份证据视图，保证汇总看到的东西和步骤一致。
    """

    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        name = str(item.get("name") or f"步骤{index}")
        lines.append(f"{index}. [{name}]")
        conclusion = str(item.get("conclusion") or "").strip()
        if conclusion:
            lines.append(f"   结论：{_compact(conclusion, 1200)}")
        findings = item.get("findings")
        if isinstance(findings, list) and findings:
            lines.append("   要点与证据：")
            for finding in findings[:12]:
                if not isinstance(finding, dict):
                    continue
                title = _compact(finding.get("title") or "", 120)
                detail = _compact(finding.get("detail") or "", 500)
                evidence = finding.get("evidence")
                ev_text = ""
                if isinstance(evidence, list):
                    ev_text = "；".join(
                        _compact(
                            f"call_id={ev.get('call_id', '')} "
                            f"excerpt={ev.get('excerpt', '')}",
                            200,
                        )
                        for ev in evidence[:3]
                        if isinstance(ev, dict)
                    )
                lines.append(f"     - {title}：{detail}" + (f"（{ev_text}）" if ev_text else ""))
        trace = item.get("tool_trace")
        if isinstance(trace, list) and trace:
            lines.append("   工具数据：")
            for call in trace[:16]:
                if not isinstance(call, dict):
                    continue
                tool_name = _compact(call.get("name") or "", 80)
                args = _compact(call.get("args"), 300)
                response = _compact(call.get("response"), 1500)
                lines.append(f"     - {tool_name}(args={args}) -> {response}")
    return lines


def build_plan_step_prompt(
    *, question: str, name: str, instruction: str, prior_summaries: list[dict[str, Any]]
) -> str:
    """步骤提示词：总目标 + 本步任务 + 前置步骤的完整证据上下文。"""

    parts = [f"总目标：{question}", f"本步任务：{name}", f"本步要求：{instruction}"]
    if prior_summaries:
        parts.append("前置步骤结果（含数据与证据，直接引用，勿重复查询相同数据）：")
        parts.extend(render_prior_context(prior_summaries))
    return "\n".join(parts) + "\n"


async def run_plan_step(
    *,
    agent: LlmAgent,
    plan_uid: str,
    step_uid: str,
    name: str,
    prompt: str,
    budget: WorkerBudget,
    publish: Callable[[dict[str, Any]], Awaitable[None]],
    stop_check: Callable[[], bool],
    session_id: str | None = None,
    on_tool_response: Callable[[str, str, Any], Awaitable[None]] | None = None,
) -> HeadlessResult:
    """跑完一个计划步骤；超时/超限/停止/异常都转成结构化结果。"""

    async def _log(line: str, tool: str = "") -> None:
        await publish(
            {
                "event": "step_log",
                "plan_uid": plan_uid,
                "step_uid": step_uid,
                "line": line,
                "tool": tool,
            }
        )

    return await run_headless_turn(
        agent=agent,
        app_name=PLAN_APP_NAME,
        prompt=prompt,
        budget=budget,
        log=_log,
        stop_check=stop_check,
        opening=f"开始执行：{name}",
        closing="步骤完成",
        session_state={HEADLESS_SESSION_ID_KEY: session_id} if session_id else None,
        on_tool_response=on_tool_response,
    )
