"""无人值守节点回合：人设/提示词构建与 headless 回合执行。

任务图节点与收尾汇总都经这里跑 headless 回合；差异通过 persona、
prompt、log 事件名参数化。工具轨迹、用量、思考过滤由 headless 统一处理。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from google.adk.agents import LlmAgent

from app.adk_runtime.headless import HeadlessResult, WorkerBudget, run_headless_turn

# headless 会话标识：任务图节点不进对话会话，用独立应用名隔离
NODE_APP_NAME = "task_graph_node"

# findings 围栏：节点以结构化 JSON 收尾，证据 call_id 必须在实收集合内。
_FINDINGS_FENCE = (
    "完成时在回答最后输出一个围栏代码块（不要省略）：\n"
    "```findings\n"
    '{"conclusion": "本节点结论摘要（1-2 句）", "findings": [{"title": "要点标题", '
    '"detail": "细节", "evidence": [{"call_id": "工具调用id", "tool": "工具名", '
    '"excerpt": "关键字段摘录"}]}]}\n'
    "```"
)


def build_node_persona(name: str, instruction: str) -> str:
    """任务节点人设：无人值守单节点执行，结构化输出结论与要点。"""

    persona = (
        f"你是任务图中的「{name}」，无人值守的单节点执行。\n"
        f"只使用只读查询工具收集证据，禁止任何写操作；工具被拒绝或报错时按原因换其他线索继续。\n"
        f"前驱节点已收集的数据、要点与工具结果都在上下文中，直接引用其中的结论和证据，"
        f"不要为获取相同数据重复调用工具。\n"
        f"调用纪律：同一工具、同一组参数最多调用一次，拿到结果就直接用；"
        f"禁止靠微调参数/换关键词反复查同一主题，同类检索只取一次，够用即止立即收尾。\n"
        f"每条要点必须给出证据：引用工具调用的 call_id 与关键字段摘录。\n"
        f"{_FINDINGS_FENCE}"
    )
    if instruction.strip():
        persona = f"{persona}\n\n本节点要求：{instruction.strip()}"
    return persona


def build_node_prompt(
    *,
    question: str,
    name: str,
    target: str,
    instruction: str,
    prior_context: list[str],
) -> str:
    """节点提示词：总目标 + 本节点任务 + 前驱节点的完整证据上下文。"""

    parts = [
        f"总目标：{question}",
        f"本节点任务：{name}",
        f"排查/执行目标：{target}",
        f"本节点要求：{instruction}",
    ]
    if prior_context:
        parts.append("前驱节点结果（含数据与证据，直接引用，勿重复查询相同数据）：")
        parts.extend(prior_context)
    return "\n".join(parts) + "\n"


async def run_node_turn(
    *,
    agent: LlmAgent,
    graph_uid: str,
    node_uid: str,
    name: str,
    prompt: str,
    budget: WorkerBudget,
    publish: Callable[[dict[str, Any]], Awaitable[None]],
    stop_check: Callable[[], bool],
    session_id: str | None = None,
    on_tool_response: Callable[[str, str, Any], Awaitable[None]] | None = None,
) -> HeadlessResult:
    """跑完一个任务节点；超时/超限/停止/异常都转成结构化结果。"""

    from app.adk_runtime.headless import HEADLESS_SESSION_ID_KEY

    async def _log(line: str, tool: str = "") -> None:
        await publish(
            {
                "event": "node_log",
                "graph_uid": graph_uid,
                "node_uid": node_uid,
                "name": name,
                "line": line,
                "tool": tool,
            }
        )

    return await run_headless_turn(
        agent=agent,
        app_name=NODE_APP_NAME,
        prompt=prompt,
        budget=budget,
        log=_log,
        stop_check=stop_check,
        opening=f"开始执行：{name}",
        closing="节点完成",
        session_state={HEADLESS_SESSION_ID_KEY: session_id} if session_id else None,
        on_tool_response=on_tool_response,
    )
