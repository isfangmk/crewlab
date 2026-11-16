"""跑完自动汇总：后台任务收口后把汇总结论写回对话流（assistant 消息）。

汇总用会话模型做一次无工具综合（失败降级为结论拼接），消息 id 固定
（auto-summary-<kind>-<uid>）保证重复触发不重复写；写入后随终态事件把
message_id 带给前端，前端重拉消息列表即可看到汇总。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession
from app.services.chat import chat_service
from app.services.platform import llm_config

logger = logging.getLogger(__name__)

# 汇总生成的预算：一次无工具综合调用，超时/失败降级为拼接文本
_SUMMARY_TIMEOUT_SECONDS = 30
_SUMMARY_MAX_TOKENS = 1200


def compose_summary_fallback(
    *, question: str, items: list[dict], gaps: list[str]
) -> str:
    """LLM 不可用时的拼接汇总正文：总目标 + 各项结论 + 缺口标注。

    标题行（完成情况）由 run_auto_summary 统一拼在正文前，这里只出正文。
    """

    lines = [f"总目标：{question}", ""]
    for item in items:
        lines.append(f"- {item['label']}（{item['status']}）：{item['conclusion']}")
    if gaps:
        lines.append("")
        lines.append("证据缺口：")
        for gap in gaps:
            lines.append(f"- {gap}")
    return "\n".join(lines)


def summary_title(*, header: str, items: list[dict]) -> str:
    """汇总卡标题：完成情况 + 收口计数（如「… · 4 路收口（3 成功 / 1 失败）」）。"""

    counts: list[str] = []
    for status, label in (("succeeded", "成功"), ("skipped", "跳过"), ("failed", "失败")):
        n = sum(1 for item in items if item["status"] == status)
        if n > 0:
            counts.append(f"{n} {label}")
    return f"{header}（{' / '.join(counts)}）" if counts else header


def build_summary_prompt(
    *, kind: str, question: str, items: list[dict], gaps: list[str]
) -> list[dict]:
    """汇总提示词：给结构化结果，让模型写综合结论（调查类引用 [n]）。

    完成情况已在标题行说明，正文不要求复述。
    """

    cite_note = (
        "用 [n] 引用发现编号（如 [1]），不要编造编号。"
        if kind == "investigation"
        else "引用步骤时写步骤名，如「产品调研」。"
    )
    parts = [
        f"总目标：{question}",
        "",
        "各项结果：",
    ]
    for item in items:
        parts.append(f"- {item['label']}（{item['status']}）：{item['conclusion']}")
    if gaps:
        parts.append("")
        parts.append("证据缺口（必须在结论末尾标注）：")
        for gap in gaps:
            parts.append(f"- {gap}")
    parts.append("")
    parts.append(
        "请写一段汇总结论：归纳核心结论，最后标注证据缺口。"
        f"{cite_note}简洁，不要复述执行过程，不要复述完成情况（标题已说明）。"
    )
    return [
        {"role": "system", "content": "你是汇总结论撰写者，只输出汇总结论正文。"},
        {"role": "user", "content": "\n".join(parts)},
    ]


async def run_auto_summary(
    db: AsyncSession,
    *,
    session_id: str,
    kind: str,
    run_uid: str,
    header: str,
    question: str,
    items: list[dict],
    gaps: list[str],
) -> str | None:
    """生成汇总并写回对话流；返回消息 id（失败返回 None，不阻塞终态）。

    kind: investigation | plan（只用于消息 id 与引用提示）；
    items: [{label, status, conclusion}]；gaps: 缺口标注文案。
    """

    message_id = f"auto-summary-{kind}-{run_uid}"
    try:
        # 幂等：同一次运行重复收口只写一条
        existing = await db.get(chat_service.ChatMessage, message_id)
        if existing is not None:
            return message_id
        body = await _generate(
            db,
            session_id=session_id,
            kind=kind,
            question=question,
            items=items,
            gaps=gaps,
        )
        # 消息内容 = 标题行（完成情况+计数）+ 空行 + 正文；前端按此拆「标题/正文」
        content = f"{summary_title(header=header, items=items)}\n\n{body}"
        await chat_service.append_assistant_message(
            db, session_id=session_id, message_id=message_id, content=content
        )
        return message_id
    except Exception:  # noqa: BLE001 - 汇总失败不阻塞终态收口
        logger.exception("自动汇总写回失败 session_id=%s kind=%s uid=%s", session_id, kind, run_uid)
        return None


async def _generate(
    db: AsyncSession,
    *,
    session_id: str,
    kind: str,
    question: str,
    items: list[dict],
    gaps: list[str],
) -> str:
    """LLM 综合汇总正文；失败降级为拼接文本（标题由调用方统一拼）。"""

    fallback = compose_summary_fallback(
        question=question, items=items, gaps=gaps
    )
    session = await db.get(ChatSession, session_id)
    if session is None:
        return fallback
    # litellm 导入很重：只在真要调模型时才导（评测裁判同款口径）
    import litellm

    try:
        provider = await llm_config.resolve_model(db, session.provider, session.model)
        litellm_model = llm_config.compose_litellm_model(
            provider_id=provider.provider_id,
            model_prefix=provider.model_prefix,
            model=session.model,
        )
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=build_summary_prompt(
                kind=kind,
                question=question,
                items=items,
                gaps=gaps,
            ),
            api_base=provider.base_url,
            api_key=provider.api_key,
            timeout=_SUMMARY_TIMEOUT_SECONDS,
            max_tokens=_SUMMARY_MAX_TOKENS,
            extra_body=llm_config.provider_extra_body(provider) or None,
        )
        # litellm 把流式 wrapper 并进了返回类型注解；这里是非流式响应，choices 实际存在
        choices = getattr(resp, "choices") or []
        text = (choices[0].message.content or "").strip() if choices else ""
        return text if text else fallback
    except Exception:  # noqa: BLE001 - 汇总模型不可用就拼接，保住「跑完有结论」
        logger.warning("汇总模型调用失败，降级拼接 session_id=%s", session_id, exc_info=True)
        return fallback
