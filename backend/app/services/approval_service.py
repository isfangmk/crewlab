"""工具审批：落库 + 通过 ADK confirmation 恢复执行。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.session_store import get_adk_session_service
from app.adk_runtime.stream_bridge import SseFrame, stream_adk_confirmation
from app.models_orm.approval import Approval
from app.models_orm.chat import ChatSession
from app.services import agent_service, chat_service, mcp_service
from app.services.llm_config import resolve_model

logger = logging.getLogger(__name__)


def approval_to_wire(a: Approval) -> dict:
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
        "result": a.result,
        "created_at": a.created_at.isoformat(),
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
    }


async def create_pending_approval(
    db: AsyncSession,
    *,
    session_id: str,
    confirmation_fc_id: str,
    tool_call_id: str,
    tool_name: str,
    hint: str,
    arguments: dict,
    turn_context: dict | None = None,
) -> Approval:
    existing = await db.scalar(
        select(Approval).where(Approval.confirmation_fc_id == confirmation_fc_id)
    )
    if existing is not None:
        return existing

    title = f"确认执行 {tool_name}"
    payload = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "confirmation_fc_id": confirmation_fc_id,
        "arguments": arguments,
        "command": hint,
        "summary": title,
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
        kind="tool_confirmation",
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


async def list_approvals(db: AsyncSession, status: str | None) -> list[Approval]:
    stmt = select(Approval).order_by(Approval.created_at.desc())
    if status is not None:
        if status == "":
            raise ValueError("status 不能为空字符串")
        stmt = stmt.where(Approval.status == status)
    result = await db.scalars(stmt)
    return list(result.all())


async def get_approval(db: AsyncSession, approval_id: str) -> Approval:
    row = await db.get(Approval, approval_id)
    if row is None:
        raise LookupError(f"审批不存在: {approval_id}")
    return row


async def pending_count(db: AsyncSession) -> int:
    rows = await list_approvals(db, "pending")
    return len(rows)


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


async def stream_decide(
    db: AsyncSession,
    *,
    approval_id: str,
    confirmed: bool,
    reason: str | None,
) -> AsyncIterator[SseFrame]:
    """审批后续跑：边执行边产出与聊天流同构的 SSE 帧（含 assistant_delta）。"""

    row = await get_approval(db, approval_id)
    if row.status != "pending":
        raise ValueError(f"审批状态不可决策: status={row.status}")

    session = await db.get(ChatSession, row.session_id)
    if session is None:
        raise LookupError(f"会话不存在: {row.session_id}")
    if session.agent_id is None or session.agent_id == "":
        raise LookupError(f"会话未绑定 agent_id: {session.id}")
    resolved_agent = await agent_service.get_agent(db, session.agent_id)

    # 全部前置校验必须在写状态之前完成：否则失败时该审批行已被置为已决，
    # 再也无法重新决策。
    turn_ctx = await _turn_context_from_approval(row)
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

    mcp_servers = await mcp_service.list_enabled_servers(db)
    mcp_servers = agent_service.filter_mcp_servers_for_agent(mcp_servers, resolved_agent)
    from app.services.tool_search_index import warm_vector_cache_from_db

    await warm_vector_cache_from_db(db)
    extra_tools = await mcp_service.build_searchable_mcp_toolsets(db, mcp_servers)
    confirm_tool_names = mcp_service.confirm_tool_names_for_servers(mcp_servers)
    # 助理级禁用名单与只读模式：恢复回合必须沿用发起时的助理约束
    disallowed_tool_names = agent_service.agent_disallowed_tool_names(resolved_agent)
    if resolved_agent.permission_mode == "read-only":
        confirm_tool_names = confirm_tool_names | set(
            mcp_service.all_business_tool_names_for_servers(mcp_servers)
        )

    now = datetime.now(timezone.utc)
    row.decided_at = now
    row.approved_by = 1
    row.reason = reason
    row.status = "approved" if confirmed else "rejected"
    await db.commit()

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
    persister = chat_service.AdkFramePersister(db, session.id, turn_context=turn_ctx)
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
            persona_instruction=resolved_agent.system_prompt,
            thinking_enabled=thinking_enabled,
            extra_tools=extra_tools,
            llm_timeout_seconds=llm_timeout_seconds,
            confirm_tool_names=confirm_tool_names,
            model_prefix=model_prefix,
            provider_extra_body=provider_extra_body,
            disallowed_tool_names=disallowed_tool_names,
            max_turns=resolved_agent.max_turns,
        ):
            await persister.persist(frame)
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
    yield SseFrame(event="approval_done", data=approval_to_wire(row))


async def _decide(
    db: AsyncSession,
    *,
    approval_id: str,
    confirmed: bool,
    reason: str | None,
) -> Approval:
    async for _frame in stream_decide(
        db,
        approval_id=approval_id,
        confirmed=confirmed,
        reason=reason,
    ):
        pass
    return await get_approval(db, approval_id)


async def approve(db: AsyncSession, approval_id: str) -> Approval:
    return await _decide(db, approval_id=approval_id, confirmed=True, reason=None)


async def reject(db: AsyncSession, approval_id: str, reason: str) -> Approval:
    return await _decide(db, approval_id=approval_id, confirmed=False, reason=reason)
