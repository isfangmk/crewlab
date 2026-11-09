"""会话 CRUD 与流式回合编排。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.session_store import get_adk_session_service
from app.adk_runtime.stream_bridge import APP_NAME, SseFrame, stream_adk_turn
from app.adk_runtime.web_search_tool import build_web_search_tool
from app.models_orm.approval import Approval
from app.models_orm.chat import ChatMessage, ChatSession
from app.schemas.chat import PageContextIn
from app.services import (
    agent_service,
    approval_service,
    app_settings_service,
    llm_config,
    mcp_service,
    observation_service,
)
from app.services.llm_config import resolve_model
from app.services import turn_control
from app.services.web_search_service import resolve_deepseek_search_cred

logger = logging.getLogger(__name__)

# 页面快照字段条数上限：防止超大快照把 prompt 撑爆。
_MAX_PAGE_CONTEXT_FIELDS = 50
_MAX_PAGE_CONTEXT_FIELD_LEN = 200


def _serialize_page_context(page_context: dict | None) -> str | None:
    """把页面快照规范化后序列化；没有引用时返回 None。

    快照来自前端，只能是「页面名 + 页面自己声明的字段」。
    字段条数与长度都设上限，超限显式失败，不静默截断。
    """

    if page_context is None:
        return None
    page = page_context.get("page")
    if not isinstance(page, str) or page == "":
        raise ValueError("page_context.page 不能为空")
    fields = page_context.get("fields", {})
    if not isinstance(fields, dict):
        raise TypeError("page_context.fields 必须是对象")
    if len(fields) > _MAX_PAGE_CONTEXT_FIELDS:
        raise ValueError(
            f"page_context.fields 条数超限: {len(fields)} > {_MAX_PAGE_CONTEXT_FIELDS}"
        )
    clean: dict[str, str] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or key == "":
            raise ValueError("page_context.fields 键无效")
        if not isinstance(value, str):
            raise TypeError(f"page_context.fields[{key}] 必须是字符串")
        if len(value) > _MAX_PAGE_CONTEXT_FIELD_LEN:
            raise ValueError(
                f"page_context.fields[{key}] 长度超限: "
                f"{len(value)} > {_MAX_PAGE_CONTEXT_FIELD_LEN}"
            )
        clean[key] = value
    return json.dumps({"page": page, "fields": clean}, ensure_ascii=False)


def _deserialize_page_context(raw: str | None) -> dict | None:
    """反序列化落库的页面快照；没有引用时返回 None。"""

    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("page_context_json 必须是对象")
    return data


def format_page_context_hint(page_context: dict | None) -> str:
    """把页面快照渲染成人设里的上下文说明。"""

    if page_context is None:
        return ""
    page = page_context.get("page")
    fields = page_context.get("fields") or {}
    lines = [f"- 当前页面：{page}"]
    for key, value in fields.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def session_page_context(session: ChatSession) -> PageContextIn | None:
    """会话引用的页面快照（对外输出形状）；没有引用时返回 None。"""

    data = _deserialize_page_context(session.page_context_json)
    if data is None:
        return None
    return PageContextIn.model_validate(data)




def normalize_tool_call(raw: dict) -> dict:
    """把落库的 tool_call 规范成与流式前端一致的结构。"""

    call_id = raw.get("id")
    if call_id is None:
        call_id = raw.get("tool_call_id")
    if not isinstance(call_id, str) or call_id == "":
        raise ValueError("tool_call 缺少 id/tool_call_id")
    name = raw.get("name")
    if not isinstance(name, str) or name == "":
        raise ValueError(f"tool_call 缺少 name: {call_id}")
    status = raw.get("status")
    if not isinstance(status, str) or status == "":
        raise ValueError(f"tool_call 缺少 status: {call_id}")
    out: dict = {
        "id": call_id,
        "name": name,
        "status": status,
    }
    if "arguments" in raw:
        out["arguments"] = raw["arguments"]
    if "arguments_raw" in raw:
        out["arguments_raw"] = raw["arguments_raw"]
    if "result" in raw:
        out["result"] = raw["result"]
    if "result_raw" in raw:
        out["result_raw"] = raw["result_raw"]
    if "error" in raw:
        out["error"] = raw["error"]
    if "duration_ms" in raw:
        out["duration_ms"] = raw["duration_ms"]
    if "device_id" in raw:
        out["device_id"] = raw["device_id"]
    return out


def _parse_attachments_json(raw: str | None, *, message_id: str) -> list[str]:
    """解析消息上的附件 id 列表；契约不符显式失败。"""

    if raw is None or raw == "":
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError(f"attachments_json 必须是数组: message_id={message_id}")
    out: list[str] = []
    for item in data:
        if not isinstance(item, str) or item == "":
            raise ValueError(f"attachments_json 含无效 id: message_id={message_id}")
        out.append(item)
    return out


def message_to_wire(m: ChatMessage, *, attachments: list[dict] | None = None) -> dict:
    """历史消息输出形状与流式渲染所用字段对齐。"""

    tool_call = None
    if m.tool_call_json is not None:
        raw = json.loads(m.tool_call_json)
        if not isinstance(raw, dict):
            raise TypeError(f"tool_call_json 必须是对象: message_id={m.id}")
        tool_call = normalize_tool_call(raw)
    # 流式里意图气泡是普通 assistant；历史用 kind 区分但不改 role
    role = m.role
    kind = m.kind
    if kind == "tool_intent":
        # 前端按 assistant 文本气泡渲染，与流式一致
        role = "assistant"
    elif kind == "tool_card":
        role = "tool"
    wire = {
        "id": m.id,
        "role": role,
        "kind": kind if kind == "tool_card" else ("tool_intent" if kind == "tool_intent" else "message"),
        "content": m.content,
        "tool_call": tool_call,
        "created_at": m.created_at.isoformat(),
    }
    if attachments is not None and len(attachments) > 0:
        wire["attachments"] = attachments
    return wire


async def messages_to_wire(db: AsyncSession, messages: list[ChatMessage]) -> list[dict]:
    """批量把消息转成前端形状，并回填用户消息附件元数据。"""

    from app.models_orm.attachment import ChatAttachment
    from app.services.attachment_service import attachment_to_wire

    needed_ids: list[str] = []
    per_message_ids: dict[str, list[str]] = {}
    for m in messages:
        ids = _parse_attachments_json(m.attachments_json, message_id=m.id)
        if len(ids) == 0:
            continue
        per_message_ids[m.id] = ids
        needed_ids.extend(ids)

    by_id: dict[str, ChatAttachment] = {}
    if len(needed_ids) > 0:
        rows = await db.scalars(
            select(ChatAttachment).where(ChatAttachment.id.in_(needed_ids))
        )
        by_id = {row.id: row for row in rows.all()}

    items: list[dict] = []
    for m in messages:
        ids = per_message_ids.get(m.id, [])
        attachments: list[dict] = []
        for attachment_id in ids:
            row = by_id.get(attachment_id)
            if row is None:
                raise LookupError(
                    f"消息引用的附件不存在: message_id={m.id} attachment_id={attachment_id}"
                )
            if row.session_id != m.session_id:
                raise ValueError(
                    f"附件不属于本会话: message_id={m.id} attachment_id={attachment_id}"
                )
            attachments.append(attachment_to_wire(row))
        items.append(message_to_wire(m, attachments=attachments))
    return items


def _normalize_turn_locale(locale: str) -> str:
    """把前端 UI 语言（en-US / zh-CN）归一到本回合输出语言。

    只接受 zh / en 两种；其他值显式失败。
    """

    if not isinstance(locale, str) or locale == "":
        raise ValueError("locale 不能为空")
    lowered = locale.lower()
    if lowered.startswith("zh"):
        return "zh"
    if lowered.startswith("en"):
        return "en"
    raise ValueError(f"locale 非法（仅支持 zh / en）: {locale}")


async def create_session(
    db: AsyncSession,
    *,
    title: str,
    provider: str,
    model: str,
    agent_id: str,
    user_id: str,
    owner_user_id: int | None = None,
    page_context: dict | None = None,
) -> ChatSession:
    await resolve_model(db, provider, model)
    await agent_service.get_agent(db, agent_id)
    session = ChatSession(
        id=str(uuid4()),
        title=title,
        provider=provider,
        model=model,
        agent_id=agent_id,
        adk_user_id=user_id,
        owner_user_id=owner_user_id,
        page_context_json=_serialize_page_context(page_context),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info(
        "创建会话 session_id=%s provider=%s model=%s agent_id=%s has_page_context=%s",
        session.id,
        provider,
        model,
        agent_id,
        page_context is not None,
    )
    return session


async def get_session(db: AsyncSession, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise LookupError(f"会话不存在: {session_id}")
    return session


_SESSION_SORT_COLUMNS = {
    "title": ChatSession.title,
    "model": ChatSession.model,
    "updated_at": ChatSession.updated_at,
    "agent_id": ChatSession.agent_id,
    "created_at": ChatSession.created_at,
}


async def list_sessions(
    db: AsyncSession,
    *,
    q: str | None = None,
    include_archived: bool = False,
    agent_id: str | None = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ChatSession], int]:
    """按筛选条件分页列出会话；total 为同条件总数。"""

    if limit < 1 or limit > 100:
        raise ValueError("limit 范围是 1..100")
    if offset < 0:
        raise ValueError("offset 不能为负")
    if updated_from is not None and updated_to is not None and updated_from > updated_to:
        raise ValueError("updated_from 不能晚于 updated_to")
    if agent_id is not None and agent_id == "":
        raise ValueError("agent_id 不能为空字符串")
    if sort_dir not in ("asc", "desc"):
        raise ValueError("sort_dir 只能是 asc 或 desc")
    if sort_by is not None and sort_by not in _SESSION_SORT_COLUMNS:
        raise ValueError(
            "sort_by 非法，允许: " + ", ".join(sorted(_SESSION_SORT_COLUMNS))
        )

    filters = []
    if not include_archived:
        filters.append(ChatSession.archived.is_(False))
    if q is not None and q != "":
        pattern = f"%{q}%"
        filters.append(
            (ChatSession.title.ilike(pattern)) | (ChatSession.model.ilike(pattern))
        )
    if agent_id is not None:
        filters.append(ChatSession.agent_id == agent_id)
    if updated_from is not None:
        filters.append(ChatSession.updated_at >= updated_from)
    if updated_to is not None:
        filters.append(ChatSession.updated_at <= updated_to)

    count_stmt = select(func.count()).select_from(ChatSession)
    list_stmt = select(ChatSession)
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    raw_total = await db.scalar(count_stmt)
    if raw_total is None:
        raise RuntimeError("会话计数返回空")
    total = int(raw_total)

    if sort_by is None:
        order = (ChatSession.pinned.desc(), ChatSession.updated_at.desc())
    else:
        col = _SESSION_SORT_COLUMNS[sort_by]
        order = (col.asc() if sort_dir == "asc" else col.desc(),)

    list_stmt = list_stmt.order_by(*order).offset(offset).limit(limit)
    items = list(await db.scalars(list_stmt))
    logger.info(
        "列出会话 q=%s agent_id=%s archived=%s from=%s to=%s sort=%s/%s limit=%s offset=%s total=%s page=%s",
        q,
        agent_id,
        include_archived,
        updated_from.isoformat() if updated_from else None,
        updated_to.isoformat() if updated_to else None,
        sort_by,
        sort_dir,
        limit,
        offset,
        total,
        len(items),
    )
    return items, total


async def update_session_flags(
    db: AsyncSession,
    session_id: str,
    *,
    pinned: bool | None = None,
    archived: bool | None = None,
) -> ChatSession:
    if pinned is None and archived is None:
        raise ValueError("pinned 与 archived 至少提供一个")
    session = await get_session(db, session_id)
    if pinned is not None:
        session.pinned = pinned
    if archived is not None:
        session.archived = archived
    await db.commit()
    await db.refresh(session)
    logger.info(
        "更新会话标记 session_id=%s pinned=%s archived=%s",
        session_id,
        session.pinned,
        session.archived,
    )
    return session


async def fork_session(
    db: AsyncSession,
    session_id: str,
    *,
    up_to_message_id: str,
    owner_user_id: int | None,
    adk_user_id: str,
) -> ChatSession:
    """复制前缀消息到新会话（不含 ADK 事件，仅业务投影）。"""

    if up_to_message_id == "":
        raise ValueError("up_to_message_id 不能为空")
    src = await get_session(db, session_id)
    messages = await list_messages(db, session_id)
    cut: ChatMessage | None = None
    for m in messages:
        if m.id == up_to_message_id:
            cut = m
            break
    if cut is None:
        raise LookupError(f"消息不存在: {up_to_message_id}")
    prefix = [m for m in messages if m.seq <= cut.seq]
    if len(prefix) == 0:
        raise ValueError("无可复制的消息前缀")
    if src.agent_id is None:
        raise ValueError("源会话缺少 agent_id，无法 fork")
    pc = session_page_context(src)
    new_session = await create_session(
        db,
        title=f"{src.title} (fork)",
        provider=src.provider,
        model=src.model,
        agent_id=src.agent_id,
        user_id=adk_user_id,
        owner_user_id=owner_user_id,
        page_context=None if pc is None else pc.model_dump(),
    )
    for i, m in enumerate(prefix, start=1):
        db.add(
            ChatMessage(
                id=str(uuid4()),
                session_id=new_session.id,
                role=m.role,
                kind=m.kind,
                content=m.content,
                tool_call_json=m.tool_call_json,
                attachments_json=m.attachments_json,
                seq=i,
            )
        )
    await db.commit()
    logger.info(
        "fork 会话 source=%s new=%s messages=%s",
        session_id,
        new_session.id,
        len(prefix),
    )
    return new_session


async def truncate_after_message(
    db: AsyncSession, session_id: str, message_id: str
) -> ChatMessage:
    """删除指定消息之后的全部投影消息，返回被保留的用户消息。"""

    if message_id == "":
        raise ValueError("message_id 不能为空")
    await get_session(db, session_id)
    messages = await list_messages(db, session_id)
    target: ChatMessage | None = None
    for m in messages:
        if m.id == message_id:
            target = m
            break
    if target is None:
        raise LookupError(f"消息不存在: {message_id}")
    if target.role != "user" or target.kind != "message":
        raise ValueError("只能编辑用户正文消息")
    for m in messages:
        if m.seq > target.seq:
            await db.delete(m)
    await db.commit()
    await db.refresh(target)
    logger.info(
        "已截断会话消息 session_id=%s keep_message_id=%s keep_seq=%s",
        session_id,
        target.id,
        target.seq,
    )
    return target


async def prepare_resend_from_user_message(
    db: AsyncSession, session_id: str, message_id: str
) -> tuple[str, list[str]]:
    """编辑重发：删除该用户消息及其后全部投影，返回原文与附件 id。"""

    if message_id == "":
        raise ValueError("message_id 不能为空")
    await get_session(db, session_id)
    messages = await list_messages(db, session_id)
    target: ChatMessage | None = None
    for m in messages:
        if m.id == message_id:
            target = m
            break
    if target is None:
        raise LookupError(f"消息不存在: {message_id}")
    if target.role != "user" or target.kind != "message":
        raise ValueError("只能对用户正文消息编辑重发")
    content = target.content if target.content is not None else ""
    if content == "":
        raise ValueError("用户消息内容为空，无法重发")
    attachment_ids: list[str] = []
    if target.attachments_json is not None and target.attachments_json != "":
        raw = json.loads(target.attachments_json)
        if not isinstance(raw, list):
            raise TypeError("attachments_json 必须是数组")
        for item in raw:
            if not isinstance(item, str) or item == "":
                raise ValueError("attachments_json 含无效 id")
            attachment_ids.append(item)
    for m in messages:
        if m.seq >= target.seq:
            await db.delete(m)
    await db.commit()
    logger.info(
        "已删除用户消息及后续投影以便重发 session_id=%s from_message_id=%s",
        session_id,
        message_id,
    )
    return content, attachment_ids


async def export_session_markdown(db: AsyncSession, session_id: str) -> str:
    session = await get_session(db, session_id)
    messages = await list_messages(db, session_id)
    lines = [
        f"# {session.title}",
        "",
        f"- provider: `{session.provider}`",
        f"- model: `{session.model}`",
        f"- agent: `{session.agent_id}`",
        "",
    ]
    for m in messages:
        if m.kind != "message":
            continue
        role = m.role
        body = m.content if m.content is not None else ""
        lines.append(f"## {role}")
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


async def list_messages(db: AsyncSession, session_id: str) -> list[ChatMessage]:
    await get_session(db, session_id)
    result = await db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq.asc())
    )
    return list(result.all())


async def rename_session(db: AsyncSession, session_id: str, title: str) -> ChatSession:
    """重命名会话标题。"""

    if title == "":
        raise ValueError("title 不能为空")
    session = await get_session(db, session_id)
    session.title = title
    await db.commit()
    await db.refresh(session)
    logger.info("重命名会话 session_id=%s", session_id)
    return session


async def update_session_model(
    db: AsyncSession, session_id: str, provider: str, model: str
) -> ChatSession:
    await resolve_model(db, provider, model)
    session = await get_session(db, session_id)
    session.provider = provider
    session.model = model
    await db.commit()
    await db.refresh(session)
    logger.info(
        "更新会话模型 session_id=%s provider=%s model=%s",
        session_id,
        provider,
        model,
    )
    return session


async def update_session_agent(
    db: AsyncSession, session_id: str, agent_id: str
) -> ChatSession:
    await agent_service.get_agent(db, agent_id)
    session = await get_session(db, session_id)
    session.agent_id = agent_id
    await db.commit()
    await db.refresh(session)
    logger.info("更新会话助理 session_id=%s agent_id=%s", session_id, agent_id)
    return session


async def delete_session(db: AsyncSession, session_id: str) -> None:
    """硬删除会话：消息 CASCADE，审批显式清，并丢掉 ADK 内存会话。"""

    session = await get_session(db, session_id)
    adk_user_id = session.adk_user_id
    await db.execute(delete(Approval).where(Approval.session_id == session_id))
    await db.delete(session)
    await db.commit()
    adk = get_adk_session_service()
    existing = await adk.get_session(
        app_name=APP_NAME, user_id=adk_user_id, session_id=session_id
    )
    if existing is not None:
        await adk.delete_session(
            app_name=APP_NAME, user_id=adk_user_id, session_id=session_id
        )
    logger.info("删除会话 session_id=%s", session_id)


async def batch_delete_sessions(db: AsyncSession, ids: list[str]) -> int:
    """批量硬删除：任一 id 不存在则整批失败，已删 0 条。"""

    if len(ids) == 0:
        raise ValueError("ids 不能为空")
    if len(ids) > 100:
        raise ValueError("ids 最多 100 个")
    if any(i == "" for i in ids):
        raise ValueError("ids 不能包含空字符串")
    if len(set(ids)) != len(ids):
        raise ValueError("ids 不能重复")

    # 先全部校验存在，避免删到一半才 404
    for session_id in ids:
        await get_session(db, session_id)
    for session_id in ids:
        await delete_session(db, session_id)
    logger.info("批量删除会话 count=%s", len(ids))
    return len(ids)


async def _next_seq(db: AsyncSession, session_id: str) -> int:
    current = await db.scalar(
        select(func.coalesce(func.max(ChatMessage.seq), 0)).where(
            ChatMessage.session_id == session_id
        )
    )
    if current is None:
        raise RuntimeError("无法计算消息序号")
    return int(current) + 1


async def _add_message(
    db: AsyncSession,
    *,
    message_id: str,
    session_id: str,
    role: str,
    kind: str,
    content: str | None,
    tool_call: dict | None,
    attachment_ids: list[str] | None = None,
) -> ChatMessage:
    seq = await _next_seq(db, session_id)
    attachments_json = None
    if attachment_ids is not None and len(attachment_ids) > 0:
        attachments_json = json.dumps(attachment_ids, ensure_ascii=False)
    msg = ChatMessage(
        id=message_id,
        session_id=session_id,
        role=role,
        kind=kind,
        content=content,
        tool_call_json=(
            json.dumps(normalize_tool_call(tool_call), ensure_ascii=False)
            if tool_call is not None
            else None
        ),
        attachments_json=attachments_json,
        seq=seq,
    )
    db.add(msg)
    await db.commit()
    return msg


async def _update_tool_card(
    db: AsyncSession,
    *,
    session_id: str,
    tool_call_id: str,
    patch: dict,
) -> None:
    """按 tool_call.id 更新已落库的工具卡，保持与流式同一条消息。"""

    rows = await db.scalars(
        select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.kind == "tool_card",
        )
    )
    target: ChatMessage | None = None
    for row in rows:
        if row.tool_call_json is None:
            continue
        raw = json.loads(row.tool_call_json)
        if not isinstance(raw, dict):
            raise TypeError(f"tool_call_json 损坏: {row.id}")
        current_id = raw.get("id") or raw.get("tool_call_id")
        if current_id == tool_call_id:
            target = row
            break
    if target is None:
        raise LookupError(f"未找到 tool_card: tool_call_id={tool_call_id}")
    raw = json.loads(target.tool_call_json or "{}")
    if not isinstance(raw, dict):
        raise TypeError(f"tool_call_json 损坏: {target.id}")
    raw.update(patch)
    target.tool_call_json = json.dumps(normalize_tool_call(raw), ensure_ascii=False)
    await db.commit()
    logger.info(
        "更新工具卡 session_id=%s tool_call_id=%s status=%s",
        session_id,
        tool_call_id,
        raw.get("status"),
    )


class AdkFramePersister:
    """把 ADK SSE 帧落到聊天消息 / 审批单；聊天流与审批恢复共用。"""

    def __init__(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        trace_id: str,
        turn_context: dict | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        # 本回合 Trace：工具/技能调用要挂到同一条观测记录上
        self._trace_id = trace_id
        # 按 message_id 分段落库，保持与 SSE 时间线一致
        self._assistant_segments: dict[str, list[str]] = {}
        self._current_assistant_id: str | None = None
        # 本回合实际使用的模型上下文；审批恢复必须沿用它
        self._turn_context: dict | None = turn_context

    async def _flush_assistant_segment(self, message_id: str) -> None:
        parts = self._assistant_segments.pop(message_id, None)
        if parts is None or len(parts) == 0:
            return
        content = "".join(parts)
        logger.debug(
            "落库助理正文 session_id=%s message_id=%s content_len=%s preview=%s",
            self._session_id,
            message_id,
            len(content),
            content if len(content) <= 80 else content[:80] + "…",
        )
        await _add_message(
            self._db,
            message_id=message_id,
            session_id=self._session_id,
            role="assistant",
            kind="message",
            content=content,
            tool_call=None,
        )

    async def persist(self, frame: SseFrame) -> None:
        """落库单帧；未知事件忽略（与原聊天流一致，只处理已知类型）。"""

        if frame.event == "tool_intent":
            await _add_message(
                self._db,
                message_id=frame.data["message_id"],
                session_id=self._session_id,
                role="assistant",
                kind="tool_intent",
                content=frame.data["content"],
                tool_call=None,
            )
            return
        if frame.event == "tool_start":
            # 工具卡落库前先刷出已到正文，避免轮询后正文跑到卡片下面
            if self._current_assistant_id is not None:
                await self._flush_assistant_segment(self._current_assistant_id)
                self._current_assistant_id = None
            # 与前端 toolCardId(`tool-card-${tool_call_id}`) 保持同一 id
            tool_call_id = frame.data["tool_call_id"]
            await observation_service.record_tool_start(
                trace_id=self._trace_id,
                tool_call_id=tool_call_id,
                name=frame.data["name"],
                arguments=json.dumps(frame.data["arguments"], ensure_ascii=False),
            )
            await _add_message(
                self._db,
                message_id=f"tool-card-{tool_call_id}",
                session_id=self._session_id,
                role="tool",
                kind="tool_card",
                content=None,
                tool_call={
                    "id": tool_call_id,
                    "name": frame.data["name"],
                    "status": frame.data["status"],
                    "arguments": frame.data["arguments"],
                },
            )
            return
        if frame.event == "tool_end":
            duration_ms = frame.data["duration_ms"]
            if not isinstance(duration_ms, int):
                raise TypeError("tool_end.duration_ms 必须是 int")
            logger.info(
                "工具调用完成 session_id=%s tool=%s tool_call_id=%s status=%s duration_ms=%s",
                self._session_id,
                frame.data["name"],
                frame.data["tool_call_id"],
                frame.data["status"],
                duration_ms,
            )
            raw_status = frame.data["status"]
            if raw_status not in ("success", "error"):
                raise ValueError(f"tool_end.status 非法: {raw_status}")
            await observation_service.record_tool_end(
                trace_id=self._trace_id,
                tool_call_id=frame.data["tool_call_id"],
                name=frame.data["name"],
                status=(
                    observation_service.STATUS_OK
                    if raw_status == "success"
                    else observation_service.STATUS_ERROR
                ),
                duration_ms=duration_ms,
                error=frame.data["error"],
            )
            await _update_tool_card(
                self._db,
                session_id=self._session_id,
                tool_call_id=frame.data["tool_call_id"],
                patch={
                    "status": frame.data["status"],
                    "result": frame.data["result"],
                    "duration_ms": duration_ms,
                    "error": frame.data["error"],
                },
            )
            return
        if frame.event == "approval_pending":
            arguments = frame.data["arguments"]
            if not isinstance(arguments, dict):
                raise TypeError("approval_pending.arguments 必须是 dict")
            command = frame.data["command"]
            if not isinstance(command, str):
                raise TypeError("approval_pending.command 必须是 str")
            approval = await approval_service.create_pending_approval(
                self._db,
                session_id=self._session_id,
                confirmation_fc_id=frame.data["confirmation_fc_id"],
                tool_call_id=frame.data["tool_call_id"],
                tool_name=frame.data["tool_name"],
                hint=command,
                arguments=arguments,
                trace_id=self._trace_id,
                turn_context=self._turn_context,
            )
            pending_result = {
                "status": "pending_approval",
                "approval_id": approval.id,
                "kind": frame.data["kind"],
                "tool_name": frame.data["tool_name"],
                "command": command,
            }
            try:
                await _update_tool_card(
                    self._db,
                    session_id=self._session_id,
                    tool_call_id=frame.data["tool_call_id"],
                    patch={"status": "pending", "result": pending_result},
                )
            except LookupError:
                await _add_message(
                    self._db,
                    message_id=f"tool-card-{frame.data['tool_call_id']}",
                    session_id=self._session_id,
                    role="tool",
                    kind="tool_card",
                    content=None,
                    tool_call={
                        "id": frame.data["tool_call_id"],
                        "name": frame.data["tool_name"],
                        "status": "pending",
                        "arguments": arguments,
                        "result": pending_result,
                    },
                )
            return
        if frame.event == "assistant_delta":
            message_id = frame.data["message_id"]
            if not isinstance(message_id, str) or message_id == "":
                raise ValueError("assistant_delta 缺少 message_id")
            delta = frame.data["delta"]
            if not isinstance(delta, str):
                raise TypeError("assistant_delta.delta 必须是 str")
            if (
                self._current_assistant_id is not None
                and self._current_assistant_id != message_id
            ):
                await self._flush_assistant_segment(self._current_assistant_id)
            self._current_assistant_id = message_id
            bucket = self._assistant_segments.get(message_id)
            if bucket is None:
                bucket = []
                self._assistant_segments[message_id] = bucket
            bucket.append(delta)
            return
        if frame.event == "done":
            if self._current_assistant_id is not None:
                await self._flush_assistant_segment(self._current_assistant_id)
                self._current_assistant_id = None
            for leftover_id, leftover_parts in list(self._assistant_segments.items()):
                if len(leftover_parts) == 0:
                    continue
                raise RuntimeError(
                    f"done 时仍有未落库助理正文 message_id={leftover_id} parts={len(leftover_parts)}"
                )


async def _hydrate_mentions(db: AsyncSession, mentions: list[dict]) -> str:
    """把 @ 提及反查成真实对象的上下文块。

    提及 id 必须对应真实存在的对象；查不到显式失败，
    不能把前端传来的 label 直接当作可信内容塞进 prompt。
    """

    if len(mentions) == 0:
        return ""
    from app.models_orm.knowledge import KnowledgeDoc
    from app.models_orm.mcp import McpServer

    lines: list[str] = []
    for mention in mentions:
        mention_type = mention["type"]
        mention_id = mention["id"]
        if mention_type == "file":
            doc = await db.get(KnowledgeDoc, mention_id)
            if doc is None:
                raise LookupError(f"提及的知识库文档不存在: {mention_id}")
            lines.append(f"- [知识库文档] {doc.title}（path={doc.path}）")
        elif mention_type == "server":
            server = await db.scalar(
                select(McpServer).where(McpServer.name == mention_id)
            )
            if server is None:
                raise LookupError(f"提及的 MCP Server 不存在: {mention_id}")
            lines.append(f"- [MCP Server] {server.name}（{server.endpoint}）")
        else:
            raise ValueError(f"不支持的提及类型: {mention_type}")
    logger.info("注入提及上下文 count=%s", len(lines))
    return "\n".join(lines)


async def _validate_attachment_ids(
    db: AsyncSession, session_id: str, attachment_ids: list[str]
) -> list[str]:
    """校验附件 id 都属于本会话；悬空或越权 id 显式失败。"""

    if len(attachment_ids) == 0:
        return []
    from app.services.attachment_service import get_attachment

    for attachment_id in attachment_ids:
        if not isinstance(attachment_id, str) or attachment_id == "":
            raise ValueError("attachment_ids 含无效 id")
        await get_attachment(db, session_id, attachment_id)
    return list(attachment_ids)


async def stream_session_message(
    db: AsyncSession,
    *,
    session_id: str,
    content: str,
    provider: str | None,
    model: str | None,
    client_message_id: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    attachment_ids: list[str],
    locale: str | None = None,
    mentions: list[dict] | None = None,
) -> AsyncIterator[SseFrame]:
    session = await get_session(db, session_id)
    use_provider = provider if provider is not None else session.provider
    use_model = model if model is not None else session.model
    user_message_id = client_message_id if client_message_id else str(uuid4())
    if client_message_id is not None and client_message_id == "":
        raise ValueError("client_message_id 不能为空字符串")

    # 附件 id 必须真实存在且属于本会话；不能接受悬空 id
    attachment_ids = await _validate_attachment_ids(db, session_id, attachment_ids)
    # @ 提及必须反查到真实对象，才能注入上下文
    mention_context = await _hydrate_mentions(db, mentions or [])

    await _add_message(
        db,
        message_id=user_message_id,
        session_id=session_id,
        role="user",
        kind="message",
        content=content,
        tool_call=None,
        attachment_ids=attachment_ids,
    )
    # 让前端把乐观用户气泡 id 换成落库 id，避免轮询后整段重挂载
    yield SseFrame(
        event="user_message",
        data={
            "message_id": user_message_id,
            "content": content,
            "attachment_ids": attachment_ids,
        },
    )

    if session.agent_id is None or session.agent_id == "":
        raise LookupError(f"会话未绑定 agent_id: {session_id}")
    resolved_agent = await agent_service.get_agent(db, session.agent_id)
    # 助理级模型覆盖优先于会话默认模型：助理页配的 model 必须真正生效。
    # 该模型必须存在于所选 provider 的模型列表里，否则显式失败。
    if resolved_agent.model != "":
        use_model = resolved_agent.model
    llm = await resolve_model(db, use_provider, use_model)
    # 回合入参全量落日志，便于线上对照模型/助理/开关/附件
    logger.info(
        "开始流式回合 session_id=%s agent_id=%s agent=%s provider=%s model=%s "
        "thinking=%s web_search=%s attachment_ids=%s content=%s",
        session_id,
        session.agent_id,
        resolved_agent.name,
        use_provider,
        use_model,
        thinking_enabled,
        web_search_enabled,
        attachment_ids,
        content,
    )
    runtime = await app_settings_service.get_agent_runtime(db)
    logger.info(
        "助理运行参数 session_id=%s write_enabled=%s llm_timeout=%s output_locale=%s",
        session_id,
        runtime["write_enabled"],
        runtime["llm_timeout_seconds"],
        runtime["output_locale"],
    )
    persona = resolved_agent.system_prompt
    # 本回合 locale 覆盖设置页 output_locale；前端传 en-US / zh-CN
    if locale is None:
        effective_locale = runtime["output_locale"]
    else:
        effective_locale = _normalize_turn_locale(locale)
    if effective_locale == "zh":
        locale_hint = "请始终使用简体中文回答。"
        persona = locale_hint if persona == "" else f"{persona}\n\n{locale_hint}"
    elif effective_locale == "en":
        locale_hint = "Always answer in English."
        persona = locale_hint if persona == "" else f"{persona}\n\n{locale_hint}"
    # @ 提及对象以真实对象信息注入人设，供模型按需检索
    if mention_context != "":
        mention_hint = "用户本轮提及了以下对象，可据此回答：\n" + mention_context
        persona = (
            mention_hint if persona == "" else f"{persona}\n\n{mention_hint}"
        )
    # 会话引用的页面快照注入人设：全局助理据此知道用户当时在看哪一页
    page_context = _deserialize_page_context(session.page_context_json)
    if page_context is not None:
        page_hint = (
            "用户本轮引用了当前页面的以下信息，可据此回答：\n"
            + format_page_context_hint(page_context)
        )
        persona = page_hint if persona == "" else f"{persona}\n\n{page_hint}"

    mcp_servers = await mcp_service.list_enabled_servers(db)
    mcp_servers = agent_service.filter_mcp_servers_for_agent(mcp_servers, resolved_agent)
    from app.services.tool_search_index import warm_vector_cache_from_db

    await warm_vector_cache_from_db(db)
    extra_tools: list = list(await mcp_service.build_searchable_mcp_toolsets(db, mcp_servers))
    confirm_tool_names = mcp_service.confirm_tool_names_for_servers(mcp_servers)
    # 助理级禁用名单：命中的工具直接拒绝执行
    disallowed_tool_names = agent_service.agent_disallowed_tool_names(resolved_agent)
    # 助理级只读模式：该助理用到的 MCP 工具一律要人工确认
    if resolved_agent.permission_mode == "read-only":
        confirm_tool_names = confirm_tool_names | set(
            mcp_service.all_business_tool_names_for_servers(mcp_servers)
        )
        logger.info(
            "助理为只读模式，所有 MCP 工具需确认 agent=%s tools=%s",
            resolved_agent.name,
            len(confirm_tool_names),
        )
    if web_search_enabled:
        search_base, search_key = await resolve_deepseek_search_cred(db)
        extra_tools.append(build_web_search_tool(base_url=search_base, api_key=search_key))
        logger.info("本回合已挂载 web_search（DeepSeek） session_id=%s", session_id)

    # 观测回合必须先建立：persister 要把工具/技能调用挂到该 trace 上
    turn_control.begin_turn(session_id)
    obs = await observation_service.start_chat_turn(
        session_id=session_id,
        agent_id=resolved_agent.name,
        provider=llm.provider_id,
        model=use_model,
        user_input=content,
    )

    persister = AdkFramePersister(
        db,
        session_id,
        trace_id=obs.trace_id,
        # 记录本回合实际使用的模型上下文；审批恢复必须沿用同一模型与超时
        turn_context={
            "provider": llm.provider_id,
            "model": use_model,
            "base_url": llm.base_url,
            "llm_timeout_seconds": int(runtime["llm_timeout_seconds"]),
            "thinking_enabled": thinking_enabled,
            "model_prefix": llm.model_prefix,
            "provider_extra_body": llm_config.provider_extra_body(llm),
            "trace_id": obs.trace_id,
        },
    )
    # 附件字节交给 ADK 多模态 parts；校验已在上方完成，这里再取内容
    from app.services.attachment_service import bytes_from_attachment, get_attachment

    image_parts: list[tuple[bytes, str]] = []
    for attachment_id in attachment_ids:
        row = await get_attachment(db, session_id, attachment_id)
        image_parts.append((bytes_from_attachment(row), row.mime_type))
    if len(image_parts) > 0:
        logger.info(
            "本回合注入图片附件 session_id=%s count=%s bytes=%s",
            session_id,
            len(image_parts),
            [len(item[0]) for item in image_parts],
        )

    stopped = False
    last_usage: dict[str, int | None] | None = None
    try:
        async for frame in stream_adk_turn(
            session_id=session_id,
            user_id=session.adk_user_id,
            user_text=content,
            image_parts=image_parts,
            provider_id=llm.provider_id,
            model=use_model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            session_service=get_adk_session_service(),
            agent_name=resolved_agent.name,
            agent_description=resolved_agent.description,
            persona_instruction=persona,
            thinking_enabled=thinking_enabled,
            extra_tools=extra_tools,
            llm_timeout_seconds=int(runtime["llm_timeout_seconds"]),
            confirm_tool_names=confirm_tool_names,
            model_prefix=llm.model_prefix,
            provider_extra_body=llm_config.provider_extra_body(llm),
            disallowed_tool_names=disallowed_tool_names,
            max_turns=resolved_agent.max_turns,
        ):
            if turn_control.is_stopped(session_id):
                logger.info("回合被停止 session_id=%s", session_id)
                stopped = True
                yield SseFrame(
                    event="done",
                    data={
                        "session_id": session_id,
                        "stopped": True,
                        "trace_id": obs.trace_id,
                    },
                )
                break
            out = frame
            if frame.event == "done" and isinstance(frame.data, dict):
                usage_raw = frame.data.get("usage")
                if isinstance(usage_raw, dict):
                    last_usage = {
                        "prompt_tokens": usage_raw.get("prompt_tokens"),
                        "completion_tokens": usage_raw.get("completion_tokens"),
                        "total_tokens": usage_raw.get("total_tokens"),
                    }
                # SseFrame 不可变，拼上 trace_id 后重建
                out = SseFrame(
                    event=frame.event,
                    data={**frame.data, "trace_id": obs.trace_id},
                )
            await persister.persist(out)
            yield out
        if stopped:
            await obs.end_stopped()
        else:
            await obs.end_ok(usage=last_usage)
    except Exception as exc:
        await obs.end_error(exc)
        raise
    finally:
        turn_control.end_turn(session_id)


async def stop_session(session_id: str) -> dict:
    """请求停止当前会话进行中的流式回合。

    空闲会话没有可停止的回合：如实返回 stopped=false，
    不能返回假的成功信号。
    """

    stopped = turn_control.request_stop(session_id)
    return {"stopped": stopped}
