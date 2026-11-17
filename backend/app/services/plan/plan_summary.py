"""计划收尾汇总：带工具的收尾回合，产出带图、带节点详情的最终回答。

与普通对话同构——用装配好的只读 agent 跑一个 headless 回合，模型可复用
步骤证据、必要时 run_python 画图；产出物落 artifacts 表并挂到汇总消息。
历史遗留：旧版用无工具的单次文本改写（auto_summary），只有干巴文字。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.headless import (
    HEADLESS_SESSION_ID_KEY,
    WorkerBudget,
    run_headless_turn,
)
from app.services.chat import artifact_service, chat_service
from app.services.plan.step_runner import render_prior_context

logger = logging.getLogger(__name__)

SUMMARY_APP_NAME = "plan_summary"

# 收尾汇总人设：复用步骤证据写最终结论，必要时画图；只描述能力与纪律。
# 直接输出正文（不用 findings 围栏）：流式增量即最终正文，前端逐字可见。
_SUMMARY_PERSONA = (
    "你是计划执行的收尾汇总者，基于前置步骤收集的全部证据写最终回答。\n"
    "前置步骤的结论、要点、证据与工具数据已在上下文中，直接引用其中的具体数据"
    "（如工作流数量、节点数、连线数、运行状态等），不要为获取相同数据重复调用工具。\n"
    "回答要给出用户要的完整结论：具体数据、节点情况、必要的对比说明，不只是笼统一句话。\n"
    "需要图表展示对比时，用 run_python 的 chart() 助手产出图表产出物；"
    "正文只写文字结论，禁止把 ECharts 配置或图表数据 JSON 写进正文。\n"
    "证据不足时如实标注缺口，不要编造数据。\n"
    "直接输出最终回答正文，不要输出工具栏、JSON、代码块或任何过程说明。"
)


def build_summary_prompt(*, question: str, steps: list[dict[str, Any]]) -> str:
    """汇总提示词：总目标 + 各步骤完整证据 + 收尾要求。"""

    parts = [f"用户目标：{question}", "", "各步骤执行结果（含数据与证据）："]
    parts.extend(render_prior_context(steps))
    parts.extend(
        [
            "",
            "请基于以上证据写出最终回答正文：完整结论 + 具体数据 + 节点情况；"
            "需要对比展示时用 run_python 的 chart() 画图。直接输出正文即可。",
        ]
    )
    return "\n".join(parts) + "\n"


def _synthetic_tool_call(call_id: str, result: dict) -> dict:
    """汇总消息的合成 run_python 卡：让前端按产出物卡渲染内联图。"""

    return {
        "id": f"summary-py-{call_id}",
        "name": "run_python",
        "status": "success",
        "arguments": {},
        "result": result,
    }


async def run_plan_summary(
    db: AsyncSession,
    *,
    session_id: str,
    plan_uid: str,
    header: str,
    question: str,
    steps: list[dict[str, Any]],
    budget: WorkerBudget,
    assemble_step: Callable[..., Awaitable[Any]],
    publish: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> str | None:
    """跑收尾回合并写回汇总消息；返回消息 id（失败降级文字，不阻塞收口）。

    assemble_step 由编排层注入（装配只读 agent），避免模块循环依赖。
    publish 传入即把正文增量以 summary_delta 推给前端（与 plan 事件同频道）。
    """

    message_id = f"auto-summary-plan-{plan_uid}"
    started = time.monotonic()
    logger.info(
        "收尾汇总开始 plan_uid=%s session_id=%s steps=%s stream=%s question=%s",
        plan_uid,
        session_id,
        len(steps),
        publish is not None,
        question[:200],
    )
    try:
        existing = await db.get(chat_service.ChatMessage, message_id)
        if existing is not None:
            logger.info("收尾汇总已存在，跳过 plan_uid=%s", plan_uid)
            return message_id

        agent = await assemble_step(
            session_id=session_id,
            name="收尾汇总",
            instruction="",
            budget=budget,
            tools=("run_python",),
            persona=_SUMMARY_PERSONA,
        )

        saved_artifact_rows: list[Any] = []

        async def _on_tool_response(call_id: str, name: str, response: Any) -> None:
            # run_python 的产出物落 artifacts 表并挂到汇总消息 id
            if name != "run_python" or not isinstance(response, dict):
                return
            try:
                rows = await artifact_service.save_from_tool_result(
                    db,
                    session_id=session_id,
                    tool_call_id=call_id,
                    tool_name=name,
                    message_id=message_id,
                )
                saved_artifact_rows.extend(rows)
                logger.info(
                    "收尾汇总产出物落库 plan_uid=%s call_id=%s count=%s",
                    plan_uid,
                    call_id,
                    len(rows),
                )
            except Exception:  # noqa: BLE001 - 产出物落库失败不拖垮汇总正文
                logger.warning("汇总产出物落库失败 call_id=%s", call_id, exc_info=True)

        async def _log(line: str, tool: str = "") -> None:
            logger.debug("汇总回合 %s tool=%s", line, tool)

        # 流式：正文增量推到 plan 事件总线，前端 relay 进对话流逐字显示。
        # 首帧带 header，与最终落库 content 结构一致，避免 refetch 对账时闪烁。
        streamed: list[str] = []

        async def _on_text_delta(delta: str, partial: bool) -> None:
            if publish is None or not delta:
                return
            streamed.append(delta)
            text = delta if streamed[:-1] else f"{header}\n\n{delta}"
            await publish(
                {
                    "event": "summary_delta",
                    "plan_uid": plan_uid,
                    "message_id": message_id,
                    "delta": text,
                    "partial": partial,
                }
            )

        turn = await run_headless_turn(
            agent=agent,
            app_name=SUMMARY_APP_NAME,
            prompt=build_summary_prompt(question=question, steps=steps),
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

        tool_call = None
        if saved_artifact_rows:
            tool_call = _synthetic_tool_call(
                plan_uid, artifact_service.tool_result_update(saved_artifact_rows)
            )
        await chat_service.append_assistant_message(
            db,
            session_id=session_id,
            message_id=message_id,
            content=content,
            tool_call=tool_call,
        )
        logger.info(
            "收尾汇总完成 plan_uid=%s status=%s duration_ms=%s body_len=%s "
            "deltas=%s artifacts=%s usage=%s",
            plan_uid,
            turn.status,
            int((time.monotonic() - started) * 1000),
            len(body),
            len(streamed),
            len(saved_artifact_rows),
            turn.usage,
        )
        return message_id
    except Exception:  # noqa: BLE001 - 汇总失败不阻塞计划终态收口
        logger.exception("计划收尾汇总失败 session_id=%s plan_uid=%s", session_id, plan_uid)
        return None
