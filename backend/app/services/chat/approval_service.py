"""工具审批：落库 + 通过 ADK confirmation 恢复执行。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.session_store import get_adk_session_service
from app.adk_runtime.sub_agents import build_sub_agents_for_agent
from app.adk_runtime.stream_bridge import SseFrame, stream_adk_confirmation
from app.models.approval import Approval
from app.models.chat import ChatSession
from app.services.agent import agent_service
from app.services.chat import chat_service
from app.services.agent.agent_tool_service import build_agent_tool_bundle
from app.services.platform.llm_config import resolve_model
from app.tools.user_input import (
    coerce_ask_display,
    normalize_user_answer,
    validate_answer,
)

logger = logging.getLogger(__name__)


def approval_to_wire(a: Approval, timeout_hours: int) -> dict:
    """出参；expires_at = created_at + 超时小时数（到点未决即失效，绝不自动批准）。"""
    return {
        "id": a.id,
        "kind": a.kind,
        "title": a.title,
        "summary": a.summary,
        "payload": a.payload,
        "source": "chat",
        "session_id": a.session_id,
        "status": a.status,
        "proposed_by": a.proposed_by,
        "approved_by": a.approved_by,
        "reason": a.reason,
        "decision_reason": a.decision_reason,
        "result": a.result,
        "created_at": a.created_at.isoformat(),
        "expires_at": (a.created_at + timedelta(hours=timeout_hours)).isoformat(),
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
    }


async def get_approval_timeout_hours(db: AsyncSession) -> int:
    """审批超时小时数（knowledge.approval_timeout_hours）；到点未决标 expired。"""

    from app.models.llm import AppSetting

    timeout_row = await db.get(AppSetting, "knowledge.approval_timeout_hours")
    if timeout_row is None:
        raise ValueError("缺少 knowledge.approval_timeout_hours")
    hours = int(timeout_row.value)
    if hours < 1:
        raise ValueError("approval_timeout_hours 必须 >= 1")
    return hours


async def create_pending_approval(
    db: AsyncSession,
    *,
    session_id: str,
    confirmation_fc_id: str,
    tool_call_id: str,
    tool_name: str,
    hint: str,
    arguments: dict,
    trace_id: str,
    turn_context: dict | None = None,
    kind: str = "tool_confirmation",
) -> Approval:
    existing = await db.scalar(
        select(Approval).where(Approval.confirmation_fc_id == confirmation_fc_id)
    )
    if existing is not None:
        return existing

    # kind 决定前端卡片形态：审批确认 vs 澄清提问
    if kind not in ("tool_confirmation", "user_input"):
        raise ValueError(f"审批 kind 非法: {kind}")
    title = "需要你的输入" if kind == "user_input" else f"确认执行 {tool_name}"
    payload = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "confirmation_fc_id": confirmation_fc_id,
        "arguments": arguments,
        "command": hint,
        "summary": title,
        # 审批恢复的工具/技能调用要归到同一 Trace
        "trace_id": trace_id,
    }
    # 记录发起本回合的实际模型上下文：审批恢复必须用同一个模型/超时/思考开关，
    # 否则与发起时的行为不一致（会话默认模型可能被单回合覆盖）。
    if turn_context is not None:
        payload["turn_context"] = turn_context
    row = Approval(
        id=confirmation_fc_id,
        session_id=session_id,
        confirmation_fc_id=confirmation_fc_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        kind=kind,
        title=title,
        summary=title,
        payload=json.dumps(payload, ensure_ascii=False),
        hint=hint,
        status="pending",
        proposed_by=1,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建审批单 approval_id=%s session_id=%s tool=%s",
        row.id,
        session_id,
        tool_name,
    )
    return row


async def expire_stale_pending(db: AsyncSession) -> int:
    """将超时未决审批标为 expired；绝不自动批准。"""

    from datetime import timedelta

    hours = await get_approval_timeout_hours(db)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        await db.scalars(select(Approval).where(Approval.status == "pending"))
    ).all()
    n = 0
    for row in rows:
        if row.created_at < cutoff:
            row.status = "expired"
            n += 1
    if n > 0:
        await db.commit()
        logger.info("已标记过期审批 count=%s cutoff_hours=%s", n, hours)
    return n


APPROVAL_STATUSES = ("pending", "approved", "executed", "rejected", "failed", "expired")


def _approval_filters(
    *,
    q: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list:
    """构造列表与状态计数共用的 WHERE 条件；非法入参直接报错。"""

    if q is not None and q == "":
        raise ValueError("q 不能为空字符串")
    if created_from is not None and created_to is not None and created_from > created_to:
        raise ValueError("created_from 不能晚于 created_to")

    filters = []
    if q is not None:
        like = f"%{q}%"
        # 关键字同时匹配标题、摘要、类型、来源工具与会话，避免只能按标题查。
        filters.append(
            or_(
                Approval.title.ilike(like),
                Approval.summary.ilike(like),
                Approval.kind.ilike(like),
                Approval.tool_name.ilike(like),
                Approval.session_id.ilike(like),
            )
        )
    if created_from is not None:
        filters.append(Approval.created_at >= created_from)
    if created_to is not None:
        filters.append(Approval.created_at <= created_to)
    return filters


async def list_approvals(
    db: AsyncSession,
    status: str | None = None,
    *,
    q: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort_dir: str = "desc",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Approval], int]:
    """分页查询审批单，返回当前页与命中总数。

    列表只按提交时间排序：sort_dir 控制升/降序，其他字段不开放排序。
    """

    if status is not None:
        if status == "":
            raise ValueError("status 不能为空字符串")
        if status not in APPROVAL_STATUSES:
            raise ValueError(f"status 取值非法: {status}")
    if sort_dir not in ("asc", "desc"):
        raise ValueError("sort_dir 只能是 asc 或 desc")
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1..200")
    if offset < 0:
        raise ValueError("offset 不能为负")

    await expire_stale_pending(db)

    filters = _approval_filters(q=q, created_from=created_from, created_to=created_to)
    if status is not None:
        filters.append(Approval.status == status)

    count_stmt = select(func.count()).select_from(Approval)
    # created_at 可能重复，补 id 保证分页顺序稳定。
    order = (
        (Approval.created_at.asc(), Approval.id.asc())
        if sort_dir == "asc"
        else (Approval.created_at.desc(), Approval.id.desc())
    )
    list_stmt = (
        select(Approval)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    total = int(await db.scalar(count_stmt) or 0)
    rows = list((await db.scalars(list_stmt)).all())
    return rows, total


async def approval_status_counts(
    db: AsyncSession,
    *,
    q: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> dict[str, int]:
    """按状态统计审批单数量，条件与列表一致（不含 status 过滤）。"""

    filters = _approval_filters(q=q, created_from=created_from, created_to=created_to)
    stmt = select(Approval.status, func.count()).group_by(Approval.status)
    if filters:
        stmt = stmt.where(*filters)
    rows = (await db.execute(stmt)).all()
    counts = {status: 0 for status in APPROVAL_STATUSES}
    for status, n in rows:
        counts[status] = int(n)
    return counts


async def get_approval(db: AsyncSession, approval_id: str) -> Approval:
    row = await db.get(Approval, approval_id)
    if row is None:
        raise LookupError(f"审批不存在: {approval_id}")
    return row


async def _claim_pending_approval(
    db: AsyncSession,
    *,
    approval_id: str,
    confirmed: bool,
    reason: str,
) -> bool:
    """把待确认的审批单认领成已决；只有仍处于 pending 时才生效。

    条件更新是原子的：两个并发决策都通过了前置检查时，只有第一个改到状态，
    第二个拿到 0 行返回 False，工具不会被执行两次。
    """

    result = await db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == "pending")
        .values(
            decided_at=datetime.now(timezone.utc),
            approved_by=1,
            reason=reason,
            decision_reason=reason,
            status="approved" if confirmed else "rejected",
        )
    )
    rowcount: int | None = getattr(result, "rowcount", None)
    return rowcount is not None and rowcount > 0


async def pending_count(db: AsyncSession) -> int:
    await expire_stale_pending(db)
    counts = await approval_status_counts(db)
    return counts["pending"]


async def _turn_context_from_approval(row: Approval) -> dict:
    """读出审批单里记录的发起回合上下文；缺失则失败，禁止回落会话默认模型。"""

    raw = json.loads(row.payload)
    if not isinstance(raw, dict):
        raise TypeError(f"审批 payload 损坏: {row.id}")
    ctx = raw.get("turn_context")
    if ctx is None:
        raise ValueError(f"审批单缺少发起回合上下文: {row.id}")
    if not isinstance(ctx, dict):
        raise TypeError(f"审批 turn_context 必须是对象: {row.id}")
    for field in (
        "provider",
        "model",
        "base_url",
        "llm_timeout_seconds",
        "thinking_enabled",
        "model_prefix",
        "provider_extra_body",
    ):
        if field not in ctx:
            raise ValueError(f"审批 turn_context 缺少字段 {field}: {row.id}")
    return ctx


async def _trace_id_from_approval(row: Approval) -> str:
    """读出审批单所属的观测 Trace，续跑的工具/技能调用必须归到同一回合。"""

    raw = json.loads(row.payload)
    if not isinstance(raw, dict):
        raise TypeError(f"审批 payload 损坏: {row.id}")
    trace_id = raw.get("trace_id")
    if not isinstance(trace_id, str) or trace_id == "":
        raise ValueError(f"审批单缺少 Trace 关联: {row.id}")
    return trace_id


def _validate_answer_for_ask(row: Approval, answer: dict) -> None:
    """按提问规格校验用户回答；规格缺失时只做形状校验。"""

    raw = json.loads(row.payload)
    options: list[str] = []
    fields: list[dict] = []
    if isinstance(raw, dict) and isinstance(raw.get("arguments"), dict):
        ask = coerce_ask_display(raw["arguments"])
        options = ask["options"]
        fields = ask["fields"]
    validate_answer(answer, options, fields)


async def stream_decide(
    db: AsyncSession,
    *,
    approval_id: str,
    confirmed: bool,
    reason: str,
    answer: dict | None = None,
    trust_scope: str | None = None,
) -> AsyncIterator[SseFrame]:
    """审批后续跑：边执行边产出与聊天流同构的 SSE 帧（含 assistant_delta）。

    answer 是澄清提问（kind=user_input）的用户回答；普通审批不带 answer。
    trust_scope 是免审范围（"tool" 仅此工具 / "all" 所有工具），仅审批闸门生效。
    """

    # 拒绝必须写原因；批准允许空说明
    if not confirmed and reason == "":
        raise ValueError("拒绝原因 reason 必填")
    row = await get_approval(db, approval_id)
    if row.status != "pending":
        raise ValueError(f"审批状态不可决策: status={row.status}")
    if answer is not None:
        if row.kind != "user_input":
            raise ValueError("仅澄清提问审批可携带 answer")
        answer = normalize_user_answer(answer)
        _validate_answer_for_ask(row, answer)
    elif row.kind == "user_input" and confirmed:
        raise ValueError("澄清提问审批必须携带 answer 提交回答")

    session = await db.get(ChatSession, row.session_id)
    if session is None:
        raise LookupError(f"会话不存在: {row.session_id}")
    if session.agent_id is None or session.agent_id == "":
        raise LookupError(f"会话未绑定 agent_id: {session.id}")
    resolved_agent = await agent_service.get_agent(db, session.agent_id)

    # 全部前置校验必须在写状态之前完成：否则失败时该审批行已被置为已决，
    # 再也无法重新决策。
    turn_ctx = await _turn_context_from_approval(row)
    trace_id = await _trace_id_from_approval(row)
    provider_id = turn_ctx["provider"]
    model = turn_ctx["model"]
    base_url = turn_ctx["base_url"]
    llm_timeout_seconds = turn_ctx["llm_timeout_seconds"]
    thinking_enabled = turn_ctx["thinking_enabled"]
    model_prefix = turn_ctx["model_prefix"]
    provider_extra_body = turn_ctx["provider_extra_body"]
    if not isinstance(llm_timeout_seconds, int):
        raise TypeError(f"turn_context.llm_timeout_seconds 必须是 int: {row.id}")
    if not isinstance(thinking_enabled, bool):
        raise TypeError(f"turn_context.thinking_enabled 必须是 bool: {row.id}")
    if not isinstance(model_prefix, str):
        raise TypeError(f"turn_context.model_prefix 必须是 str: {row.id}")
    if not isinstance(provider_extra_body, dict):
        raise TypeError(f"turn_context.provider_extra_body 必须是 dict: {row.id}")
    llm = await resolve_model(db, provider_id, model)

    # 审批恢复回合必须与发起时装配同一套技能、MCP 白名单与审批约束
    web_search_enabled = turn_ctx.get("web_search_enabled", False)
    if not isinstance(web_search_enabled, bool):
        raise TypeError(f"turn_context.web_search_enabled 必须是 bool: {row.id}")
    tool_bundle = await build_agent_tool_bundle(
        db,
        resolved_agent,
        web_search_enabled=web_search_enabled,
    )
    runtime_skills = tool_bundle.runtime_skills
    extra_tools = tool_bundle.extra_tools
    confirm_tool_names = tool_bundle.confirm_tool_names
    disallowed_tool_names = tool_bundle.disallowed_tool_names

    # 决策落库走原子认领：两个并发决策都通过检查时，只有第一个改到状态
    if not await _claim_pending_approval(
        db,
        approval_id=row.id,
        confirmed=confirmed,
        reason=reason,
    ):
        raise ValueError(f"审批已被处理: {approval_id}")
    if answer is not None:
        # 用户回答落 payload：卡片已答态回显与事后追溯都从这里读
        payload_raw = json.loads(row.payload)
        if not isinstance(payload_raw, dict):
            raise TypeError(f"审批 payload 损坏: {row.id}")
        payload_raw["answer"] = answer
        row.payload = json.dumps(payload_raw, ensure_ascii=False)
    await db.commit()
    await db.refresh(row)

    tool_result: dict | None = None
    logger.info(
        "审批决策开始 approval_id=%s session_id=%s agent=%s provider=%s model=%s "
        "tool=%s tool_call_id=%s confirmed=%s timeout=%s thinking=%s",
        approval_id,
        session.id,
        resolved_agent.name,
        provider_id,
        model,
        row.tool_name,
        row.tool_call_id,
        confirmed,
        llm_timeout_seconds,
        thinking_enabled,
    )
    # 与聊天流同一套落库：续跑里的二次审批 / 后续工具卡不能丢
    persister = chat_service.AdkFramePersister(
        db,
        session.id,
        trace_id=trace_id,
        turn_context=turn_ctx,
    )
    try:
        async for frame in stream_adk_confirmation(
            session_id=session.id,
            user_id=session.adk_user_id,
            confirmation_fc_id=row.confirmation_fc_id,
            tool_call_id=row.tool_call_id,
            confirmed=confirmed,
            provider_id=llm.provider_id,
            model=model,
            base_url=base_url,
            api_key=llm.api_key,
            session_service=get_adk_session_service(),
            agent_name=resolved_agent.name,
            agent_description=resolved_agent.description,
            persona_instruction=await agent_service.compose_persona(db, resolved_agent),
            thinking_enabled=thinking_enabled,
            extra_tools=extra_tools,
            llm_timeout_seconds=llm_timeout_seconds,
            confirm_tool_names=confirm_tool_names,
            model_prefix=model_prefix,
            provider_extra_body=provider_extra_body,
            disallowed_tool_names=disallowed_tool_names,
            max_turns=resolved_agent.max_turns,
            knowledge_scope=resolved_agent.knowledge_scope,
            skills=runtime_skills,
            answer=answer,
            trust_scope=trust_scope,
            sub_agents=await build_sub_agents_for_agent(
                db,
                resolved_agent,
                provider_id=provider_id,
                model=model,
                base_url=base_url,
                api_key=llm.api_key,
                model_prefix=model_prefix,
                provider_extra_body=provider_extra_body,
                thinking_enabled=thinking_enabled,
                web_search_enabled=web_search_enabled,
            ),
        ):
            frame = await persister.persist(frame)
            if frame.event == "tool_end" and frame.data.get("tool_call_id") == row.tool_call_id:
                duration_ms = frame.data["duration_ms"]
                if not isinstance(duration_ms, int):
                    raise TypeError("tool_end.duration_ms 必须是 int")
                logger.info(
                    "审批后工具完成 session_id=%s tool=%s tool_call_id=%s status=%s duration_ms=%s",
                    session.id,
                    frame.data["name"],
                    row.tool_call_id,
                    frame.data["status"],
                    duration_ms,
                )
                tool_result = {
                    "status": frame.data["status"],
                    "result": frame.data["result"],
                    "error": frame.data["error"],
                }
            yield frame
    except Exception as exc:
        # 执行失败必须落到 failed 状态并保留原因，不能留下已决但无结果的行
        row = await get_approval(db, approval_id)
        row.status = "failed"
        row.result = f"审批后执行失败: {exc}"
        await db.commit()
        await db.refresh(row)
        logger.exception("审批后执行失败 approval_id=%s", approval_id)
        raise

    row = await get_approval(db, approval_id)
    if confirmed:
        if tool_result is None:
            row.status = "failed"
            row.result = "审批通过后未收到工具执行结果"
        elif tool_result.get("status") == "error":
            row.status = "failed"
            row.result = json.dumps(tool_result.get("result"), ensure_ascii=False)
        else:
            row.status = "executed"
            row.executed_at = datetime.now(timezone.utc)
            row.result = json.dumps(tool_result.get("result"), ensure_ascii=False)
    else:
        row.status = "rejected"
        # reason 已在决策开始时写入 row.reason；工具卡/结果必须带回，不能写死文案
        if reason == "":
            reject_text = "用户拒绝执行"
        else:
            reject_text = f"用户拒绝执行：{reason}"
        row.result = reject_text
        await chat_service._update_tool_card(
            db,
            session_id=session.id,
            tool_call_id=row.tool_call_id,
            patch={
                "status": "error",
                "error": reject_text,
                "result": {"error": reject_text, "reason": reason},
            },
        )
    await db.commit()
    await db.refresh(row)
    logger.info(
        "审批决策完成 approval_id=%s status=%s confirmed=%s",
        row.id,
        row.status,
        confirmed,
    )
    yield SseFrame(
        event="approval_done",
        data=approval_to_wire(row, await get_approval_timeout_hours(db)),
    )


async def _decide(
    db: AsyncSession,
    *,
    approval_id: str,
    confirmed: bool,
    reason: str,
    answer: dict | None = None,
    trust_scope: str | None = None,
) -> Approval:
    async for _frame in stream_decide(
        db,
        approval_id=approval_id,
        confirmed=confirmed,
        reason=reason,
        answer=answer,
        trust_scope=trust_scope,
    ):
        pass
    return await get_approval(db, approval_id)


async def approve(
    db: AsyncSession,
    approval_id: str,
    *,
    reason: str = "",
    answer: dict | None = None,
    trust_scope: str | None = None,
) -> Approval:
    row = await _decide(
        db,
        approval_id=approval_id,
        confirmed=True,
        reason=reason,
        answer=answer,
        trust_scope=trust_scope,
    )
    row.decision_reason = reason
    await db.commit()
    await db.refresh(row)
    return row


async def reject(db: AsyncSession, approval_id: str, reason: str) -> Approval:
    if reason == "":
        raise ValueError("拒绝原因 reason 必填")
    row = await _decide(db, approval_id=approval_id, confirmed=False, reason=reason)
    row.decision_reason = reason
    await db.commit()
    await db.refresh(row)
    return row


async def batch_decide(
    db: AsyncSession,
    *,
    ids: list[str],
    confirmed: bool,
    reason: str = "",
) -> list[Approval]:
    if not confirmed and reason == "":
        raise ValueError("拒绝原因 reason 必填")
    if len(ids) == 0:
        raise ValueError("ids 不能为空")
    out: list[Approval] = []
    for approval_id in ids:
        if confirmed:
            out.append(await approve(db, approval_id, reason=reason))
        else:
            out.append(await reject(db, approval_id, reason))
    return out
