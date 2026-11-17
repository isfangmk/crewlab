"""任务图收尾汇总：带工具的收尾回合，产出带图、带节点详情的最终回答。

用装配好的只读 agent 跑一个 headless 回合，模型复用全部节点证据、必要时
run_python 画图；产出物落 artifacts 表并挂到汇总消息。正文增量经
summary_delta 推给前端逐字显示；右下角 stats 缺键不写、禁止假 0。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.headless import HEADLESS_SESSION_ID_KEY, WorkerBudget, run_headless_turn
from app.models.artifact import Artifact
from app.services.chat import chat_service
from app.services.execution import artifacts as graph_artifacts
from app.services.execution.context import render_prior_context

logger = logging.getLogger(__name__)

SUMMARY_APP_NAME = "task_graph_summary"

# 收尾汇总人设：复用节点证据写最终结论，必要时画图；只描述能力与纪律。
# 直接输出正文（不用 findings 围栏）：流式增量即最终正文，前端逐字可见。
SUMMARY_PERSONA = (
    "你是任务图执行的收尾汇总者，基于全部节点收集到的证据写最终回答。\n"
    "各节点的结论、要点、证据与工具数据已在上下文中，直接引用其中的具体数据"
    "（如数量、状态、关键指标等），不要为获取相同数据重复调用工具。\n"
    "回答要给出用户要的完整结论：具体数据、节点情况、必要的对比说明，不只是笼统一句话。\n"
    "需要图表展示对比时，用 run_python 的 chart() 助手产出图表产出物；"
    "正文只写文字结论，禁止把 ECharts 配置或图表数据 JSON 写进正文。\n"
    "图表位置：每产出一张图，就在正文讨论到该项对比的那一段末尾单独写一行锚点 "
    "[[图:标题关键词]]（标题关键词取该图表 chart() 的 title 里的几个字，便于对上）；"
    "锚点必须放在与图表内容最相关的段落，不要全部堆到回答最后。"
    "若某段没有对应图表就不写锚点。\n"
    "证据不足时如实标注缺口，不要编造数据。\n"
    "严格约束：你的回复的第一个字就是给用户看的最终答案正文。"
    "禁止输出任何自我分析、推理、思考过程、自问自答、"
    "『我需要』『让我』『The prior step』『分析：』之类的铺垫文字，"
    "无论中文英文都不允许；直接给出结论正文。"
)


def build_summary_prompt(*, question: str, nodes: list[dict[str, Any]]) -> str:
    """汇总提示词：总目标 + 各节点完整证据 + 收尾要求。"""

    parts = [f"用户目标：{question}", "", "各节点执行结果（含数据与证据）："]
    parts.extend(render_prior_context(nodes))
    parts.extend(
        [
            "",
            "请基于以上证据写出最终回答正文：完整结论 + 具体数据 + 节点情况；"
            "需要对比展示时用 run_python 的 chart() 画图，并在讨论该项的段落末尾写 [[图:标题关键词]] 锚点，"
            "把图表定位到正文相关处而非全部堆在最后。直接输出正文即可。",
        ]
    )
    return "\n".join(parts) + "\n"


async def run_graph_summary(
    db: AsyncSession,
    *,
    session_id: str,
    graph_uid: str,
    header: str,
    question: str,
    nodes: list[dict[str, Any]],
    budget: WorkerBudget,
    assemble_agent_fn: Callable[..., Awaitable[Any]],
    graph_stats: dict[str, int] | None = None,
    publish: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> str | None:
    """跑收尾回合并写回汇总消息；返回消息 id（失败降级，不阻塞收口）。

    graph_stats 是全图口径（各节点 token 之和 + 墙钟耗时），用于汇总消息的
    stats：用户看到的用量/耗时应覆盖整张图，而非只有收尾汇总这一次 LLM 调用。
    """

    message_id = f"auto-summary-taskgraph-{graph_uid}"
    started = time.monotonic()
    logger.info(
        "任务图收尾汇总开始 graph_uid=%s session_id=%s nodes=%s stream=%s question=%s",
        graph_uid,
        session_id,
        len(nodes),
        publish is not None,
        question[:200],
    )
    try:
        existing = await db.get(chat_service.ChatMessage, message_id)
        if existing is not None:
            logger.info("任务图收尾汇总已存在，跳过 graph_uid=%s", graph_uid)
            return message_id

        agent = await assemble_agent_fn(
            session_id=session_id,
            name="收尾汇总",
            target=question,
            instruction="",
            budget=budget,
            tools=("run_python",),
            persona=SUMMARY_PERSONA,
            allow_run_python=True,
        )

        saved_artifact_rows: list[Any] = []

        async def _on_tool_response(call_id: str, name: str, response: Any) -> None:
            rows = await graph_artifacts.persist_tool_response(
                db,
                session_id=session_id,
                message_id=message_id,
                call_id=call_id,
                name=name,
                response=response,
            )
            saved_artifact_rows.extend(rows)

        async def _log(line: str, tool: str = "") -> None:
            logger.debug("任务图汇总回合 %s tool=%s", line, tool)

        streamed: list[str] = []

        async def _on_text_delta(delta: str, partial: bool) -> None:
            if publish is None or not delta:
                return
            streamed.append(delta)
            text = delta if streamed[:-1] else f"{header}\n\n{delta}"
            await publish(
                {
                    "event": "summary_delta",
                    "graph_uid": graph_uid,
                    "message_id": message_id,
                    "delta": text,
                    "partial": partial,
                }
            )

        turn = await run_headless_turn(
            agent=agent,
            app_name=SUMMARY_APP_NAME,
            prompt=build_summary_prompt(question=question, nodes=nodes),
            budget=budget,
            log=_log,
            stop_check=lambda: False,
            opening="开始收尾汇总",
            closing="收尾汇总完成",
            session_state={HEADLESS_SESSION_ID_KEY: session_id},
            on_tool_response=_on_tool_response,
            on_text_delta=_on_text_delta if publish is not None else None,
        )

        body = turn.conclusion.strip() or "（汇总未产出结论）"
        content = f"{header}\n\n{body}"

        # 汇总自己没画新图时复用节点已落库的产物，保证"说有图就有图"。
        display_rows = saved_artifact_rows
        if not display_rows:
            node_rows = (
                await db.scalars(
                    select(Artifact)
                    .where(
                        Artifact.session_id == session_id,
                        Artifact.message_id.like("taskgraph-node-%"),
                    )
                    .order_by(Artifact.created_at.asc(), Artifact.seq.asc())
                )
            ).all()
            display_rows = list(node_rows)
            if display_rows:
                logger.info(
                    "任务图汇总复用节点产物 graph_uid=%s count=%s",
                    graph_uid,
                    len(display_rows),
                )

        tool_call = (
            graph_artifacts.synthetic_tool_call(graph_uid, display_rows)
            if display_rows
            else None
        )

        # 汇总消息的 stats 用全图口径：节点 token/墙钟耗时 + 收尾汇总自身的 token。
        # 否则用户只看到收尾这一次调用的 3.xk token / 几十秒，误以为整图就这点用量。
        stats: dict[str, int] = dict(graph_stats or {})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = turn.usage.get(key)
            if isinstance(value, int):
                stats[key] = stats.get(key, 0) + value

        await chat_service.append_assistant_message(
            db,
            session_id=session_id,
            message_id=message_id,
            content=content,
            tool_call=tool_call,
            stats=stats or None,
        )
        logger.info(
            "任务图收尾汇总完成 graph_uid=%s status=%s duration_ms=%s body_len=%s "
            "deltas=%s artifacts=%s usage=%s",
            graph_uid,
            turn.status,
            int((time.monotonic() - started) * 1000),
            len(body),
            len(streamed),
            len(saved_artifact_rows),
            turn.usage,
        )
        return message_id
    except Exception:  # noqa: BLE001 - 汇总失败不阻塞图终态收口
        logger.exception("任务图收尾汇总失败 session_id=%s graph_uid=%s", session_id, graph_uid)
        return None
