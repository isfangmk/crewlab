"""计划步骤执行：单步无人值守 agent 的人设、上下文与执行。

步骤复用 worker 装配（只读工具 + 知识检索 + 继承 MCP + 禁写闸门），
只换人设与只读白名单（计划步骤允许 run_python 做汇总分析）；
执行走共用的无人值守回合引擎（adk_runtime.headless）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent

from app.adk_runtime.headless import HeadlessResult, WorkerBudget, run_headless_turn
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


def build_plan_step_prompt(
    *, question: str, name: str, instruction: str, prior_summaries: list[tuple[str, str]]
) -> str:
    """步骤提示词：总目标 + 本步任务 + 前置步骤结论（顺序执行的上下文累积）。"""

    parts = [f"总目标：{question}", f"本步任务：{name}", f"本步要求：{instruction}"]
    if prior_summaries:
        parts.append("前置步骤结论（供参考，可直接引用）：")
        for i, (step_name, summary) in enumerate(prior_summaries, start=1):
            parts.append(f"{i}. [{step_name}] {summary}")
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
    )
