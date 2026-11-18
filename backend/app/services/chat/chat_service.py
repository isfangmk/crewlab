"""会话 CRUD 与流式回合编排。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from google.adk.events import Event
from google.genai import types
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.context_compaction import resolve_context_compaction_threshold
from app.adk_runtime.naming import root_adk_name
from app.adk_runtime.session_store import get_adk_session_service
from app.adk_runtime.stream_bridge import APP_NAME, SseFrame, stream_adk_turn
from app.adk_runtime.sub_agents import build_sub_agents_for_agent
from app.adk_runtime.tool_result_budget import resolve_max_tool_result_chars
from app.models.approval import Approval
from app.models.artifact import Artifact
from app.models.attachment import ChatAttachment
from app.models.chat import ChatMessage, ChatSession
from app.models.observation import ObservationTurn
from app.models.share import ShareToken
from app.schemas.chat import PageContextIn
from app.services.agent import agent_service
from app.services.agent.agent_tool_service import build_agent_tool_bundle
from app.services.chat import approval_service, artifact_service, turn_control
from app.services.knowledge import memory_service
from app.services.observability import observation_service
from app.services.platform import app_settings_service, llm_config
from app.services.platform.llm_config import resolve_model
from app.tools.user_input import coerce_ask_display

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




def _tool_call_ids_of(messages: list[ChatMessage]) -> list[str]:
    """从投影消息里取工具调用 id；截断/分叉时用于关联 Artifact。"""

    ids: set[str] = set()
    for m in messages:
        if not m.tool_call_json:
            continue
        try:
            raw = json.loads(m.tool_call_json)
        except json.JSONDecodeError:
            logger.warning("tool_call_json 解析失败 message_id=%s", m.id)
            continue
        if not isinstance(raw, dict):
            continue
        call_id = raw.get("id") or raw.get("tool_call_id")
        if isinstance(call_id, str) and call_id != "":
            ids.add(call_id)
    return sorted(ids)


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
        # tool_card / tool_intent / thinking 原样透传；其余历史 kind 统一为 message
        "kind": kind if kind in ("tool_card", "tool_intent", "thinking") else "message",
        "content": m.content,
        "tool_call": tool_call,
        "created_at": m.created_at.isoformat(),
        # 回答统计（耗时 / token）；无统计为 None，禁止假 0
        "stats": json.loads(m.stats_json) if m.stats_json else None,
        # 该消息是否为插话打断时写入（被截断的半句），供前端标注
        "interrupted": bool(m.interrupted),
    }
    if attachments is not None and len(attachments) > 0:
        wire["attachments"] = attachments
    return wire


async def messages_to_wire(db: AsyncSession, messages: list[ChatMessage]) -> list[dict]:
    """批量把消息转成前端形状，并回填用户消息附件元数据。"""

    from app.models.attachment import ChatAttachment
    from app.services.chat.attachment_service import attachment_to_wire

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
    thinking_enabled: bool = False,
    web_search_enabled: bool = False,
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
        thinking_enabled=thinking_enabled,
        web_search_enabled=web_search_enabled,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info(
        "创建会话（初值，生效值见首条消息的『会话生效模型』） "
        "session_id=%s provider=%s model=%s agent_id=%s has_page_context=%s",
        session.id,
        session.provider,
        session.model,
        agent_id,
        page_context is not None,
    )
    return session


async def get_session(db: AsyncSession, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None:
        raise LookupError(f"会话不存在: {session_id}")
    return session


# 「最后一条消息时间」是相关子查询：发消息只写 chat_messages，不会 touch 会话行，
# 直接用 chat_sessions.updated_at 会把「置顶/改名」当成「最近对话」。
_LAST_MESSAGE_AT = (
    select(func.max(ChatMessage.created_at))
    .where(ChatMessage.session_id == ChatSession.id)
    .correlate(ChatSession)
    .scalar_subquery()
)

_SESSION_SORT_COLUMNS = {
    "title": ChatSession.title,
    "model": ChatSession.model,
    "updated_at": ChatSession.updated_at,
    "last_message_at": _LAST_MESSAGE_AT,
    "agent_id": ChatSession.agent_id,
    "created_at": ChatSession.created_at,
}

# 列表里的「最近一条摘要」只用于辨认会话，长度下限必须固定：
# 超长回答整段塞进列表响应会把 20 条的分页变成几百 KB。
_SESSION_PREVIEW_MAX_CHARS = 240


@dataclass(frozen=True)
class SessionStats:
    """会话列表附带的统计：轮数、工具/技能调用、最近活动与运行信号。

    message_count 只统计 user / assistant 的正文消息：tool_card / tool_intent
    也是 chat_messages 的行，混进来会让「消息数」随工具调用次数虚高。
    turn_count 是用户提问次数（一问一答算一轮），列表用它当「轮数」展示。
    工具 / 技能调用与 tokens 一律取 observation_turns：那是唯一按回合记账的地方，
    用消息行数推算会把多步工具链算成一次。
    """

    message_count: int
    turn_count: int
    tool_call_count: int
    skill_call_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    total_tokens: int | None
    last_turn_status: str | None
    pending_approvals: int
    pending_inputs: int
    attachment_count: int
    artifact_count: int
    shared: bool


async def list_session_stats(
    db: AsyncSession, session_ids: list[str]
) -> dict[str, SessionStats]:
    """批量取会话统计，七条聚合查询覆盖整页，避免逐行 N+1。

    预览取「最近一条有正文的消息」：tool_card / tool_intent 的 content 为空，
    用它们当摘要只会显示空行。运行信号（工具/技能调用、tokens、最近回合状态）
    来自 observation_turns；没有观测记录时保持 None / 0，列表不会瞎编。
    """

    if not session_ids:
        return {}

    message_rows = (
        await db.execute(
            select(
                ChatMessage.session_id,
                func.count()
                .filter(ChatMessage.role.in_(("user", "assistant")))
                .label("message_count"),
                func.count().filter(ChatMessage.role == "user").label("turn_count"),
                func.max(ChatMessage.created_at).label("last_message_at"),
            )
            .where(ChatMessage.session_id.in_(session_ids))
            .group_by(ChatMessage.session_id)
        )
    ).all()
    message_stats = {
        sid: (int(message_count), int(turn_count), last_message_at)
        for sid, message_count, turn_count, last_message_at in message_rows
    }

    # 窗口函数按 seq 倒序取每个会话的第一条有正文消息，结果集只等于会话数。
    # 不用 PG 的 DISTINCT ON：它在 SQLite 上会被静默降级成普通 DISTINCT，
    # 取哪一行就不再由 seq 决定（摘要会变成旧消息）。
    preview_window = (
        select(
            ChatMessage.session_id.label("sid"),
            ChatMessage.content.label("content"),
            func.row_number()
            .over(
                partition_by=ChatMessage.session_id,
                order_by=ChatMessage.seq.desc(),
            )
            .label("rn"),
        )
        .where(
            ChatMessage.session_id.in_(session_ids),
            ChatMessage.content.is_not(None),
            ChatMessage.content != "",
        )
        .subquery()
    )
    preview_rows = (
        await db.execute(
            select(preview_window.c.sid, preview_window.c.content).where(
                preview_window.c.rn == 1
            )
        )
    ).all()
    previews = {
        sid: content.strip()[:_SESSION_PREVIEW_MAX_CHARS]
        for sid, content in preview_rows
        if content is not None and content.strip() != ""
    }

    # 一条窗口查询同时拿「累计调用数 / tokens」与「最近一回合状态」：
    # 直接 group by 只能给聚合值，拿不到最新状态那一行。
    observation_window = (
        select(
            ObservationTurn.session_id.label("session_id"),
            ObservationTurn.status.label("status"),
            func.sum(ObservationTurn.total_tokens)
            .over(partition_by=ObservationTurn.session_id)
            .label("total_tokens"),
            func.sum(ObservationTurn.tool_call_count)
            .over(partition_by=ObservationTurn.session_id)
            .label("tool_call_count"),
            func.sum(ObservationTurn.skill_call_count)
            .over(partition_by=ObservationTurn.session_id)
            .label("skill_call_count"),
            func.row_number()
            .over(
                partition_by=ObservationTurn.session_id,
                order_by=(
                    ObservationTurn.started_at.desc(),
                    ObservationTurn.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(ObservationTurn.session_id.in_(session_ids))
        .subquery()
    )
    observation_rows = (
        await db.execute(
            select(
                observation_window.c.session_id,
                observation_window.c.status,
                observation_window.c.total_tokens,
                observation_window.c.tool_call_count,
                observation_window.c.skill_call_count,
            ).where(observation_window.c.rn == 1)
        )
    ).all()
    observations = {
        sid: (
            None if total_tokens is None else int(total_tokens),
            str(status),
            int(tool_call_count),
            int(skill_call_count),
        )
        for sid, status, total_tokens, tool_call_count, skill_call_count in observation_rows
    }

    # 审批与「需要输入」是两种交互，分开计数：待审只算操作确认，待输入只算澄清提问
    approval_rows = (
        await db.execute(
            select(Approval.session_id, func.count())
            .where(
                Approval.session_id.in_(session_ids),
                Approval.status == "pending",
                Approval.kind == "tool_confirmation",
            )
            .group_by(Approval.session_id)
        )
    ).all()
    pending = {sid: int(n) for sid, n in approval_rows}

    input_rows = (
        await db.execute(
            select(Approval.session_id, func.count())
            .where(
                Approval.session_id.in_(session_ids),
                Approval.status == "pending",
                Approval.kind == "user_input",
            )
            .group_by(Approval.session_id)
        )
    ).all()
    pending_inputs = {sid: int(n) for sid, n in input_rows}

    attachment_rows = (
        await db.execute(
            select(ChatAttachment.session_id, func.count())
            .where(ChatAttachment.session_id.in_(session_ids))
            .group_by(ChatAttachment.session_id)
        )
    ).all()
    attachments = {sid: int(n) for sid, n in attachment_rows}

    artifact_rows = (
        await db.execute(
            select(Artifact.session_id, func.count())
            .where(Artifact.session_id.in_(session_ids))
            .group_by(Artifact.session_id)
        )
    ).all()
    artifacts = {sid: int(n) for sid, n in artifact_rows}

    # 只算「还没过期」的分享：过期令牌在页面上点不开，标「已分享」会误导
    shared_rows = (
        await db.execute(
            select(ShareToken.session_id)
            .where(
                ShareToken.session_id.in_(session_ids),
                ShareToken.expires_at > datetime.now(timezone.utc),
            )
            .distinct()
        )
    ).all()
    shared = {sid for (sid,) in shared_rows}

    stats: dict[str, SessionStats] = {}
    for sid in session_ids:
        message_count, turn_count, last_message_at = message_stats.get(
            sid, (0, 0, None)
        )
        total_tokens, last_turn_status, tool_call_count, skill_call_count = (
            observations.get(sid, (None, None, 0, 0))
        )
        stats[sid] = SessionStats(
            message_count=message_count,
            turn_count=turn_count,
            tool_call_count=tool_call_count,
            skill_call_count=skill_call_count,
            last_message_at=last_message_at,
            last_message_preview=previews.get(sid),
            total_tokens=total_tokens,
            last_turn_status=last_turn_status,
            pending_approvals=pending.get(sid, 0),
            pending_inputs=pending_inputs.get(sid, 0),
            attachment_count=attachments.get(sid, 0),
            artifact_count=artifacts.get(sid, 0),
            shared=sid in shared,
        )
    return stats


async def list_sessions(
    db: AsyncSession,
    *,
    q: str | None = None,
    include_archived: bool = False,
    agent_id: str | None = None,
    model: str | None = None,
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
    if model is not None and model == "":
        raise ValueError("model 不能为空字符串")
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
    if model is not None:
        # 模型筛选是精确匹配：q 的模糊匹配会同时命中标题含模型名的会话，选不准。
        filters.append(ChatSession.model == model)
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
        # 默认按「最近活动」倒序：没有消息的新会话回落到创建时间
        order = (
            ChatSession.pinned.desc(),
            func.coalesce(_LAST_MESSAGE_AT, ChatSession.created_at).desc(),
        )
    else:
        col = _SESSION_SORT_COLUMNS[sort_by]
        if sort_by == "last_message_at":
            # 没有消息的会话排最后，不能让 NULL 在 DESC 下顶到第一页
            order = (
                col.asc().nulls_last() if sort_dir == "asc" else col.desc().nulls_last(),
            )
        else:
            order = (col.asc() if sort_dir == "asc" else col.desc(),)

    list_stmt = list_stmt.order_by(*order).offset(offset).limit(limit)
    items = list(await db.scalars(list_stmt))
    logger.info(
        "列出会话 q=%s agent_id=%s model=%s archived=%s from=%s to=%s sort=%s/%s limit=%s offset=%s total=%s page=%s",
        q,
        agent_id,
        model,
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


async def search_messages(
    db: AsyncSession,
    *,
    q: str,
    limit: int,
    owner_user_id: int,
    is_admin: bool = False,
) -> list[dict]:
    """跨会话搜索消息正文，按会话聚合命中片段；仅返回当前用户可见的会话。

    SQLite/PG 跨方言用 ilike + 转义（复用 knowledge_service 的口径）；
    属主过滤在 ChatSession 上做，避免跨用户内容泄露。admin 放行。
    """

    from app.services.knowledge.knowledge_service import _escape_like

    if not q.strip():
        raise ValueError("q 不能为空")
    if limit < 1 or limit > 100:
        raise ValueError("limit 范围是 1..100")

    pattern = f"%{_escape_like(q.strip())}%"
    # 仅正文消息，排除工具卡/思考段（搜索用户真正说过/助理真正答过的内容）
    stmt = (
        select(ChatMessage, ChatSession.title)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatMessage.content.ilike(pattern, escape="\\"),
            ChatMessage.role.in_(["user", "assistant"]),
            ChatMessage.kind.in_(["message", None]),
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit * 4)  # 多取一些以便按会话聚合后仍有足够条目
    )
    if not is_admin:
        stmt = stmt.where(
            or_(
                ChatSession.owner_user_id == owner_user_id,
                (ChatSession.owner_user_id.is_(None) & (ChatSession.adk_user_id == str(owner_user_id))),
            )
        )
    rows = (await db.execute(stmt)).all()

    # 按会话聚合：每个会话只保留最近的一条命中，避免一条会话刷屏。
    by_session: dict[str, dict] = {}
    for msg, session_title in rows:
        if msg.session_id in by_session:
            continue
        content = (msg.content or "").replace("\n", " ").strip()
        by_session[msg.session_id] = {
            "session_id": msg.session_id,
            "session_title": session_title,
            "message_id": msg.id,
            "role": msg.role,
            "snippet": content[:200],
        }
        if len(by_session) >= limit:
            break
    logger.info("搜索消息 q=%s owner=%s hits=%s sessions=%s", q, owner_user_id, len(rows), len(by_session))
    return list(by_session.values())


async def list_session_models(db: AsyncSession) -> list[str]:
    """会话里实际出现过的模型名，供列表筛选项使用。

    含归档会话：筛选与「含归档」开关是正交的，隐藏归档会让选项随开关跳变。
    模型筛选是精确匹配，选项必须来自真实数据，否则选了也查不到。
    """

    rows = await db.scalars(
        select(ChatSession.model).distinct().order_by(ChatSession.model)
    )
    return [m for m in rows.all() if m != ""]


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


async def update_session_prefs(
    db: AsyncSession,
    session_id: str,
    *,
    thinking_enabled: bool | None = None,
    web_search_enabled: bool | None = None,
) -> ChatSession:
    if thinking_enabled is None and web_search_enabled is None:
        raise ValueError("thinking_enabled 与 web_search_enabled 至少提供一个")
    session = await get_session(db, session_id)
    if thinking_enabled is not None:
        session.thinking_enabled = thinking_enabled
    if web_search_enabled is not None:
        session.web_search_enabled = web_search_enabled
    await db.commit()
    await db.refresh(session)
    logger.info(
        "更新会话开关 session_id=%s thinking_enabled=%s web_search_enabled=%s",
        session_id,
        session.thinking_enabled,
        session.web_search_enabled,
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
        # fork 沿用源会话的「思考 / 联网搜索」开关
        thinking_enabled=src.thinking_enabled,
        web_search_enabled=src.web_search_enabled,
    )
    # 附件按会话归属：复制前缀消息时必须把附件行一并复制到新会话，
    # 否则新会话的消息会引用旧会话的附件而被拒绝渲染。
    prefix_attachment_ids = {
        attachment_id
        for m in prefix
        for attachment_id in _parse_attachments_json(m.attachments_json, message_id=m.id)
    }
    attachment_id_map: dict[str, str] = {}
    if prefix_attachment_ids:
        rows = await db.scalars(
            select(ChatAttachment).where(ChatAttachment.id.in_(prefix_attachment_ids))
        )
        rows_by_id = {row.id: row for row in rows.all()}
        for old_id in sorted(prefix_attachment_ids):
            row = rows_by_id.get(old_id)
            if row is None:
                raise LookupError(f"消息引用的附件不存在: attachment_id={old_id}")
            if row.session_id != session_id:
                raise ValueError(f"附件不属于源会话: attachment_id={old_id}")
            new_id = str(uuid4())
            attachment_id_map[old_id] = new_id
            db.add(
                ChatAttachment(
                    id=new_id,
                    session_id=new_session.id,
                    name=row.name,
                    mime_type=row.mime_type,
                    size=row.size,
                    data_url=row.data_url,
                )
            )
    message_id_map: dict[str, str] = {}
    for i, m in enumerate(prefix, start=1):
        old_attachment_ids = _parse_attachments_json(m.attachments_json, message_id=m.id)
        attachments_json = (
            json.dumps(
                [attachment_id_map[old_id] for old_id in old_attachment_ids],
                ensure_ascii=False,
            )
            if old_attachment_ids
            else m.attachments_json
        )
        new_message_id = str(uuid4())
        message_id_map[m.id] = new_message_id
        db.add(
            ChatMessage(
                id=new_message_id,
                session_id=new_session.id,
                role=m.role,
                kind=m.kind,
                content=m.content,
                tool_call_json=m.tool_call_json,
                attachments_json=attachments_json,
                seq=i,
            )
        )
    copied_artifacts = await artifact_service.copy_for_prefix(
        db,
        source_session_id=session_id,
        target_session_id=new_session.id,
        message_id_map=message_id_map,
        tool_call_ids=_tool_call_ids_of(prefix),
    )
    await db.commit()
    logger.info(
        "fork 会话 source=%s new=%s messages=%s artifacts=%s",
        session_id,
        new_session.id,
        len(prefix),
        copied_artifacts,
    )
    await _replay_adk_events(
        session=new_session,
        history=[(m.role, m.kind, m.content) for m in prefix],
    )
    return new_session


async def _replay_adk_events(
    *,
    session: ChatSession,
    history: list[tuple[str, str, str | None]],
) -> None:
    """按业务投影重建 ADK 事件流。

    编辑重发 / 分叉后 ADK 侧必须与截断后的消息一致，否则模型会看到
    用户已经删掉的历史。工具调用没有完整的 wire 记录，只回放正文；
    调用次数与落库数量会写日志，便于排查上下文差异。
    """

    adk = get_adk_session_service()
    existing = await adk.get_session(
        app_name=APP_NAME, user_id=session.adk_user_id, session_id=session.id
    )
    if existing is not None:
        await adk.delete_session(
            app_name=APP_NAME, user_id=session.adk_user_id, session_id=session.id
        )
    adk_session = await adk.create_session(
        app_name=APP_NAME, user_id=session.adk_user_id, session_id=session.id
    )
    replayed = 0
    # 回放是紧循环：Windows 时钟粒度约 15.6ms，连续事件会拿到相同时间戳，
    # ADK 按 (timestamp, id) 排序时 id 是随机 UUID，历史顺序会被打乱。
    # 这里显式给每条回放事件递增微秒，保证顺序稳定。
    base_ts = time.time()
    for index, (role, kind, content) in enumerate(history):
        if kind != "message" or role not in ("user", "assistant"):
            continue
        text = (content or "").strip()
        if text == "":
            continue
        is_user = role == "user"
        await adk.append_event(
            session=adk_session,
            event=Event(
                author=(
                    "user"
                    if is_user
                    else root_adk_name(
                        getattr(session, "agent_id", None) or "default"
                    )
                ),
                timestamp=base_ts + index * 1e-6,
                content=types.Content(
                    role="user" if is_user else "model",
                    parts=[types.Part(text=text)],
                ),
            ),
        )
        replayed += 1
    logger.info(
        "已重建 ADK 事件流 session_id=%s 投影=%s 回放=%s",
        session.id,
        len(history),
        replayed,
    )


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
    removed = [m for m in messages if m.seq > target.seq]
    await artifact_service.delete_for_messages(
        db,
        session_id=session_id,
        message_ids=[m.id for m in removed],
        tool_call_ids=_tool_call_ids_of(removed),
    )
    for m in removed:
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
    session = await get_session(db, session_id)
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
    removed = [m for m in messages if m.seq >= target.seq]
    await artifact_service.delete_for_messages(
        db,
        session_id=session_id,
        message_ids=[m.id for m in removed],
        tool_call_ids=_tool_call_ids_of(removed),
    )
    for m in removed:
        await db.delete(m)
    await db.commit()
    logger.info(
        "已删除用户消息及后续投影以便重发 session_id=%s from_message_id=%s",
        session_id,
        message_id,
    )
    remaining = await list_messages(db, session_id)
    await _replay_adk_events(
        session=session,
        history=[(m.role, m.kind, m.content) for m in remaining],
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


async def append_assistant_message(
    db: AsyncSession, *, session_id: str, message_id: str, content: str,
    tool_call: dict | None = None,
    stats: dict | None = None,
) -> ChatMessage:
    """带外补写 assistant 正文消息（自动汇总等后台产出）。

    与回合产出共用 _add_message 收口：seq 排序、字段规整口径一致。
    tool_call 用于汇总消息内联产出物（合成 run_python 卡）；
    stats 用于右下角显示耗时/token（与普通回答同口径）。
    """

    return await _add_message(
        db,
        message_id=message_id,
        session_id=session_id,
        role="assistant",
        kind="message",
        content=content,
        tool_call=tool_call,
        stats=stats,
    )


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


def session_model_sync_target(
    session: ChatSession, provider: str | None, model: str | None
) -> tuple[str, str] | None:
    """本回合请求显式选模时返回会话待写回的 (provider, model)，否则 None。

    会话模型是本回合与下游（计划步骤/排查 worker）的唯一源；请求模型不写回
    就会「建计划用请求模型、执行用旧会话模型」。编辑重发/trigger/publish 传
    None 沿用会话模型，这里返回 None 不写回。
    """

    if provider is None or model is None:
        return None
    if session.provider == provider and session.model == model:
        return None
    return provider, model


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
    stats: dict | None = None,
    interrupted: bool = False,
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
        stats_json=json.dumps(stats, ensure_ascii=False) if stats else None,
        seq=seq,
        interrupted=interrupted,
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
        abort_signal: asyncio.Event | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        # 本回合 Trace：工具/技能调用要挂到同一条观测记录上
        self._trace_id = trace_id
        # 按 message_id 分段落库，保持与 SSE 时间线一致；
        # 思考段（kind=thinking）与正文段同款缓冲，历史回放要能看到思考过程
        self._assistant_segments: dict[str, list[str]] = {}
        self._current_assistant_id: str | None = None
        self._current_assistant_key: str | None = None
        self._thinking_segments: dict[str, list[str]] = {}
        self._current_thinking_id: str | None = None
        # (kind, message_id) 首次到达顺序：跨类型刷库时按此序落行，时间线不乱序
        self._segment_order: list[tuple[str, str]] = []
        # 已落库的行 id：同一 message_id 跨边界再到达时换后缀行，防主键撞车丢文本
        self._written_ids: set[str] = set()
        # 本回合实际使用的模型上下文；审批恢复必须沿用它
        self._turn_context: dict | None = turn_context
        # 插话打断：done 帧带 interrupted 时打标记；同时持有 abort_signal 供段落提前
        # flush 时实时判定（中断信号可能在 done 帧之前到达，缓冲已落库则 done 标记不到）。
        self._interrupted: bool = False
        self._abort_signal = abort_signal

    def _buffer_delta(self, kind: str, message_id: str, delta: str) -> str:
        """缓冲正文/思考增量，返回本段实际行 id；首次到达记序保证跨类型落库顺序。"""

        segments = (
            self._thinking_segments if kind == "thinking" else self._assistant_segments
        )
        key = message_id
        if key in self._written_ids:
            n = 2
            while f"{message_id}~{n}" in self._written_ids:
                n += 1
            key = f"{message_id}~{n}"
        bucket = segments.get(key)
        if bucket is None:
            bucket = []
            segments[key] = bucket
            self._segment_order.append((kind, key))
        bucket.append(delta)
        return key

    async def _flush_segment(
        self, kind: str, message_id: str, stats: dict | None = None
    ) -> None:
        segments = (
            self._thinking_segments if kind == "thinking" else self._assistant_segments
        )
        parts = segments.pop(message_id, None)
        if parts is None or len(parts) == 0:
            return
        content = "".join(parts)
        logger.debug(
            "落库 %s session_id=%s message_id=%s content_len=%s",
            "思考段" if kind == "thinking" else "助理正文",
            self._session_id,
            message_id,
            len(content),
        )
        self._written_ids.add(message_id)
        # 中断标记取两处并集：done 帧显式带 interrupted，或 abort_signal 已置位。
        # 后者覆盖「段落因段切换在 done 前提前 flush、done 时 buffer 已空」的情形。
        interrupted = self._interrupted or (
            self._abort_signal is not None and self._abort_signal.is_set()
        )
        await _add_message(
            self._db,
            message_id=message_id,
            session_id=self._session_id,
            role="assistant",
            kind="thinking" if kind == "thinking" else "message",
            content=content,
            tool_call=None,
            stats=stats,
            interrupted=interrupted,
        )

    async def _flush_all(
        self, *, stats_for: str | None = None, stats: dict | None = None
    ) -> None:
        """按到达顺序落库全部缓冲段；回合统计只挂 stats_for 对应的正文段。"""

        order = self._segment_order
        self._segment_order = []
        self._current_assistant_id = None
        self._current_assistant_key = None
        self._current_thinking_id = None
        for kind, message_id in order:
            await self._flush_segment(
                kind,
                message_id,
                stats if (kind == "message" and message_id == stats_for) else None,
            )

    async def persist(self, frame: SseFrame) -> SseFrame:
        """落库单帧，并返回要下发给前端的帧（解释器产出物落库后补全结果）。"""

        if frame.event == "tool_intent":
            # 先落此前缓冲的正文/思考，意图行不能插到它们前面
            await self._flush_all()
            await _add_message(
                self._db,
                message_id=frame.data["message_id"],
                session_id=self._session_id,
                role="assistant",
                kind="tool_intent",
                content=frame.data["content"],
                tool_call=None,
            )
            return frame
        if frame.event == "tool_start":
            # 工具卡落库前先刷出已到正文/思考，避免轮询后它们跑到卡片下面
            await self._flush_all()
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
            return frame
        if frame.event == "tool_end":
            await self._flush_all()
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
            # 代码解释器等工具的产出物在 tool_end 时落库：
            # 完整 payload 走进程内暂存（工具侧 remember_pending），落库后把
            # 含 artifact id/payload 的瘦身载荷写回 tool_card，并随本帧下发前端，
            # 使实时会话与历史回放看到同一份可渲染数据。
            result = frame.data["result"]
            artifacts_update: dict | None = None
            artifact_error: str | None = None
            if frame.data["name"] == "run_python" and isinstance(result, dict):
                try:
                    saved = await artifact_service.save_from_tool_result(
                        self._db,
                        session_id=self._session_id,
                        tool_call_id=frame.data["tool_call_id"],
                        tool_name=frame.data["name"],
                    )
                except Exception as exc:  # noqa: BLE001 — 产出物失败不能拖垮整条消息流
                    logger.exception(
                        "Artifact 落库失败 session_id=%s tool_call_id=%s",
                        self._session_id,
                        frame.data["tool_call_id"],
                    )
                    artifact_error = f"产出物保存失败: {exc}"
                    result = {**result, "artifact_error": artifact_error}
                else:
                    if saved:
                        artifacts_update = artifact_service.tool_result_update(saved)
                        result = {**result, **artifacts_update}
            await _update_tool_card(
                self._db,
                session_id=self._session_id,
                tool_call_id=frame.data["tool_call_id"],
                patch={
                    "status": frame.data["status"],
                    "result": result,
                    "duration_ms": duration_ms,
                    "error": frame.data["error"],
                },
            )
            if artifacts_update is not None or artifact_error is not None:
                return SseFrame(event=frame.event, data={**frame.data, "result": result})
            return frame
        if frame.event == "approval_pending":
            await self._flush_all()
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
                kind=frame.data["kind"],
            )
            if frame.data["kind"] == "user_input":
                # 澄清提问卡：把提问规格写进信封，历史回放可原样重渲染
                pending_result = {
                    "status": "pending_input",
                    "approval_id": approval.id,
                    "kind": "user_input",
                    "tool_name": frame.data["tool_name"],
                    **coerce_ask_display(arguments),
                }
            else:
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
            return frame
        if frame.event == "assistant_delta":
            message_id = frame.data["message_id"]
            if not isinstance(message_id, str) or message_id == "":
                raise ValueError("assistant_delta 缺少 message_id")
            delta = frame.data["delta"]
            if not isinstance(delta, str):
                raise TypeError("assistant_delta.delta 必须是 str")
            if message_id != self._current_assistant_id:
                # 新段开写：先按到达顺序落库此前缓冲的段（含思考段）
                await self._flush_all()
                self._current_assistant_id = message_id
            self._current_assistant_key = self._buffer_delta("message", message_id, delta)
            return frame
        if frame.event == "thinking_delta":
            message_id = frame.data["message_id"]
            if not isinstance(message_id, str) or message_id == "":
                raise ValueError("thinking_delta 缺少 message_id")
            delta = frame.data["delta"]
            if not isinstance(delta, str):
                raise TypeError("thinking_delta.delta 必须是 str")
            if message_id != self._current_thinking_id:
                await self._flush_all()
                self._current_thinking_id = message_id
            self._buffer_delta("thinking", message_id, delta)
            return frame
        if frame.event == "done":
            # 插话打断：done 帧带 interrupted=True 时，本回合缓冲的正文段打标记落库
            data = frame.data if isinstance(frame.data, dict) else {}
            self._interrupted = bool(data.get("interrupted"))
            # 运行统计（耗时 / token）挂到本运行最后一条 assistant 气泡；缺失键不写，禁止假 0
            stats: dict = {}
            duration_ms = data.get("duration_ms")
            if isinstance(duration_ms, int):
                stats["duration_ms"] = duration_ms
            usage = data.get("usage")
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int):
                        stats[key] = value
            await self._flush_all(
                stats_for=self._current_assistant_key,
                stats=stats if stats else None,
            )
            if self._assistant_segments or self._thinking_segments:
                raise RuntimeError(
                    "done 时仍有未落库正文/思考段"
                    f" assistant={list(self._assistant_segments)} thinking={list(self._thinking_segments)}"
                )
        return frame


async def _hydrate_mentions(db: AsyncSession, mentions: list[dict]) -> str:
    """把 @ 提及反查成真实对象的上下文块。

    提及 id 必须对应真实存在的对象；查不到显式失败，
    不能把前端传来的 label 直接当作可信内容塞进 prompt。
    """

    if len(mentions) == 0:
        return ""
    from app.models.agent import UserAgent
    from app.models.knowledge import KnowledgeDoc
    from app.models.mcp import McpServer
    from app.models.skill import UserSkill
    from app.models.workflow import Workflow

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
        elif mention_type == "skill":
            skill = await db.get(UserSkill, mention_id)
            if skill is None:
                raise LookupError(f"提及的技能不存在: {mention_id}")
            lines.append(f"- [技能] {skill.key}（{skill.description[:100]}）")
        elif mention_type == "workflow":
            wf = await db.get(Workflow, int(mention_id))
            if wf is None:
                raise LookupError(f"提及的工作流不存在: {mention_id}")
            lines.append(f"- [工作流] {wf.name}（{wf.description[:100]}）")
        elif mention_type == "agent":
            agent = await db.get(UserAgent, mention_id)
            if agent is None:
                raise LookupError(f"提及的助理不存在: {mention_id}")
            lines.append(f"- [助理] {agent.display_name or agent.name}（{agent.description[:100]}）")
        elif mention_type == "session":
            sess = await db.get(ChatSession, mention_id)
            if sess is None:
                raise LookupError(f"提及的会话不存在: {mention_id}")
            lines.append(f"- [会话] {sess.title}（id={sess.id}）")
        else:
            raise ValueError(f"不支持的提及类型: {mention_type}")
    logger.info("注入提及上下文 count=%s", len(lines))
    return "\n".join(lines)


async def _hydrate_attachment_hint(
    db: AsyncSession, session_id: str, attachment_ids: list[str]
) -> str:
    """把本轮附件清单（含 id/类型）注入模型上下文，供工具按 id 取用。

    zip 技能包不作为 inline part 发给 LiteLlm（其 MIME 不被支持，见
    attachment_service.is_inline_image），模型必须拿到 attachment_id 才能调用
    install_skill_from_zip。图片同样列出 id，便于需要时引用。
    """

    if len(attachment_ids) == 0:
        return ""
    from app.services.chat.attachment_service import ZIP_MIME_TYPES, get_attachment

    lines: list[str] = []
    for attachment_id in attachment_ids:
        row = await get_attachment(db, session_id, attachment_id)
        kind = "技能包(zip)" if row.mime_type in ZIP_MIME_TYPES else "图片"
        lines.append(
            f"- [{kind}] 文件名={row.name} attachment_id={row.id} mime={row.mime_type}"
        )
    logger.info("注入附件上下文 session_id=%s count=%s", session_id, len(lines))
    return "\n".join(lines)


async def _validate_attachment_ids(
    db: AsyncSession, session_id: str, attachment_ids: list[str]
) -> list[str]:
    """校验附件 id 都属于本会话；悬空或越权 id 显式失败。"""

    if len(attachment_ids) == 0:
        return []
    from app.services.chat.attachment_service import get_attachment

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
    channel: str = "",
    abort_signal: asyncio.Event | None = None,
) -> AsyncIterator[SseFrame]:
    session = await get_session(db, session_id)
    use_provider = provider if provider is not None else session.provider
    use_model = model if model is not None else session.model
    sync_target = session_model_sync_target(session, provider, model)
    if sync_target is not None:
        # 用户显式选模：校验后写回会话，下游（计划步骤/排查 worker）读同一份。
        # 建会话时记的是请求初值，这里才是本回合真正生效的模型，单独留痕。
        previous = (session.provider, session.model)
        await resolve_model(db, *sync_target)
        session.provider, session.model = sync_target
        await db.commit()
        logger.info(
            "会话生效模型 session_id=%s from=%s/%s to=%s/%s",
            session_id,
            previous[0],
            previous[1],
            sync_target[0],
            sync_target[1],
        )
    user_message_id = client_message_id if client_message_id else str(uuid4())
    if client_message_id is not None and client_message_id == "":
        raise ValueError("client_message_id 不能为空字符串")

    # 附件 id 必须真实存在且属于本会话；不能接受悬空 id
    attachment_ids = await _validate_attachment_ids(db, session_id, attachment_ids)
    # @ 提及必须反查到真实对象，才能注入上下文
    mention_context = await _hydrate_mentions(db, mentions or [])
    # 附件清单（含 zip 的 id）注入上下文：模型据此调用 install_skill_from_zip 等工具
    attachment_context = await _hydrate_attachment_hint(db, session_id, attachment_ids)

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
    # 工具结果上限：助理级覆盖 > 全局默认 > 内置默认（单一收口解析）
    effective_max_tool_result_chars = resolve_max_tool_result_chars(
        resolved_agent.max_tool_result_chars,
        int(runtime["max_tool_result_chars"]),
    )
    # 上下文压缩阈值：助理级覆盖（0 = 关闭）> 全局默认 > 内置默认（关闭）
    effective_context_compaction_threshold = resolve_context_compaction_threshold(
        resolved_agent.context_compaction_threshold,
        int(runtime["context_compaction_threshold"]),
    )
    logger.info(
        "助理运行参数 session_id=%s write_enabled=%s llm_timeout=%s output_locale=%s "
        "max_tool_result_chars=%s (agent_override=%s) "
        "context_compaction_threshold=%s (agent_override=%s)",
        session_id,
        runtime["write_enabled"],
        runtime["llm_timeout_seconds"],
        runtime["output_locale"],
        effective_max_tool_result_chars,
        resolved_agent.max_tool_result_chars,
        effective_context_compaction_threshold,
        resolved_agent.context_compaction_threshold,
    )
    persona = await agent_service.compose_persona(db, resolved_agent)
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
    # 本轮上传的附件（含 zip id）注入人设：zip 需按 id 调 install_skill_from_zip
    if attachment_context != "":
        attachment_hint = (
            "用户本轮上传了以下附件，可按 attachment_id 使用对应工具：\n"
            + attachment_context
        )
        persona = (
            attachment_hint if persona == "" else f"{persona}\n\n{attachment_hint}"
        )
    # 会话引用的页面快照注入人设：全局助理据此知道用户当时在看哪一页
    page_context = _deserialize_page_context(session.page_context_json)
    if page_context is not None:
        page_hint = (
            "用户本轮引用了当前页面的以下信息，可据此回答：\n"
            + format_page_context_hint(page_context)
        )
        persona = page_hint if persona == "" else f"{persona}\n\n{page_hint}"
    # 长期记忆注入人设：跨会话事实/偏好自动进上下文，不依赖模型自觉调 recall_memory
    memory_hint = await memory_service.compose_memory_hint(
        db, agent_id=session.agent_id or None
    )
    if memory_hint != "":
        persona = memory_hint if persona == "" else f"{persona}\n\n{memory_hint}"

    # 助理能力装配：技能、MCP 白名单、禁用名单、只读审批与联网搜索。
    # 子助理与审批续跑复用同一套口径。
    tool_bundle = await build_agent_tool_bundle(
        db,
        resolved_agent,
        web_search_enabled=web_search_enabled,
    )
    runtime_skills = tool_bundle.runtime_skills
    extra_tools = tool_bundle.extra_tools
    confirm_tool_names = tool_bundle.confirm_tool_names
    disallowed_tool_names = tool_bundle.disallowed_tool_names
    rule_actions = tool_bundle.rule_actions

    # 观测回合必须先建立：persister 要把工具/技能调用挂到该 trace 上
    await turn_control.begin_turn(session_id)
    obs = await observation_service.start_chat_turn(
        session_id=session_id,
        agent_id=resolved_agent.name,
        provider=llm.provider_id,
        model=use_model,
        user_input=content,
        channel=channel,
    )

    persister = AdkFramePersister(
        db,
        session_id,
        trace_id=obs.trace_id,
        abort_signal=abort_signal,
        # 记录本回合实际使用的模型上下文；审批恢复必须沿用同一模型与超时
        turn_context={
            "provider": llm.provider_id,
            "model": use_model,
            "base_url": llm.base_url,
            "llm_timeout_seconds": int(runtime["llm_timeout_seconds"]),
            "max_tool_result_chars": effective_max_tool_result_chars,
            "context_compaction_threshold": effective_context_compaction_threshold,
            "thinking_enabled": thinking_enabled,
            "web_search_enabled": web_search_enabled,
            "model_prefix": llm.model_prefix,
            "provider_extra_body": llm_config.provider_extra_body(llm),
            "trace_id": obs.trace_id,
        },
    )
    # 附件字节交给 ADK 多模态 parts：只有图片能作为 inline content 发给 LiteLlm。
    # zip 技能包是给 install_skill_from_zip 工具用的（工具按 attachment_id 从库里取字节），
    # 不能作为 inline part 发 Model——LiteLlm 不支持 application/x-zip-compressed，
    # 会在 _content_to_message_param 阶段直接抛错，整个回合失败。
    from app.services.chat.attachment_service import (
        bytes_from_attachment,
        get_attachment,
        is_inline_image,
    )

    image_parts: list[tuple[bytes, str]] = []
    for attachment_id in attachment_ids:
        row = await get_attachment(db, session_id, attachment_id)
        if not is_inline_image(row.mime_type):
            continue
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
            rule_actions=rule_actions,
            max_turns=resolved_agent.max_turns,
            max_tool_result_chars=effective_max_tool_result_chars,
            context_compaction_threshold=effective_context_compaction_threshold,
            knowledge_scope=resolved_agent.knowledge_scope,
            skills=runtime_skills,
                sub_agents=await build_sub_agents_for_agent(
                    db,
                    resolved_agent,
                    provider_id=llm.provider_id,
                    model=use_model,
                    base_url=llm.base_url,
                    api_key=llm.api_key,
                    model_prefix=llm.model_prefix,
                    provider_extra_body=llm_config.provider_extra_body(llm),
                    thinking_enabled=thinking_enabled,
                    web_search_enabled=web_search_enabled,
                ),
                abort_signal=abort_signal,
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
            if frame.event == "assistant_delta":
                # 首个正文增量到达即记录 TTFT；思考增量不计入
                obs.mark_first_token()
            out = frame
            if frame.event == "done" and isinstance(frame.data, dict):
                # 插话打断：abort_signal 被置位说明本回合是被中断收尾的，
                # 给 done 帧打 interrupted=True，persister 据此把半句标记为 interrupted 落库。
                if abort_signal is not None and abort_signal.is_set():
                    out = SseFrame(
                        event=frame.event,
                        data={**frame.data, "interrupted": True},
                    )
                usage_raw = frame.data.get("usage")
                if isinstance(usage_raw, dict):
                    last_usage = {
                        "prompt_tokens": usage_raw.get("prompt_tokens"),
                        "completion_tokens": usage_raw.get("completion_tokens"),
                        "total_tokens": usage_raw.get("total_tokens"),
                    }
                # SseFrame 不可变，拼上 trace_id 后重建
                out = SseFrame(
                    event=out.event,
                    data={**out.data, "trace_id": obs.trace_id},
                )
            out = await persister.persist(out)
            yield out
        if stopped:
            await obs.end_stopped()
        else:
            await obs.end_ok(usage=last_usage)
    except Exception as exc:
        await obs.end_error(exc)
        raise
    finally:
        await turn_control.end_turn(session_id)


async def stop_session(session_id: str) -> dict:
    """请求停止当前会话进行中的流式回合。

    空闲会话没有可停止的回合：如实返回 stopped=false，
    不能返回假的成功信号。
    """

    stopped = await turn_control.request_stop(session_id)
    return {"stopped": stopped}
