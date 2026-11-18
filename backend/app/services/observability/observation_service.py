"""对话可观测：回合 Trace 写入 Postgres，按 session 查询。"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.observation import ObservationToolCall, ObservationTurn
from app.request_context import get_request_id

logger = logging.getLogger(__name__)

_INPUT_SUMMARY_MAX = 500
_STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
_STATUS_STOPPED = "stopped"

# 技能经 ADK SkillToolset 暴露为加载类工具；这些名字都算技能调用。
_SKILL_TOOL_NAMES = {"load_skill", "list_skills"}


def count_result_chars(value: object) -> int:
    """统计回喂模型的工具结果字符数：递归累加所有字符串叶子，与治理器同口径。

    非字符串标量（数字/布尔/None）几乎不占 token，忽略；容器按结构递归。
    这样 dict/list 结果不会因 str() 的括号与引号而被高估。
    """

    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(count_result_chars(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(count_result_chars(v) for v in value)
    return 0


def classify_tool(name: str) -> tuple[str, str]:
    """返回 (kind, detail)。

    kind: skill | mcp | tool；detail 存技能名或 MCP server 名，普通工具为空。
    """

    if name in _SKILL_TOOL_NAMES:
        return "skill", ""
    if name.startswith("mcp__"):
        # 命名规则 mcp__{server}__{tool}，server 段保留数据库原值
        parts = name.split("__")
        if len(parts) >= 3 and parts[1] != "":
            return "mcp", parts[1]
    return "tool", ""


@dataclass
class ChatTurnObservation:
    """进行中的一轮观测句柄。"""

    trace_id: str
    _started_at: float
    _first_token_ms: int | None = None

    def mark_first_token(self) -> None:
        """记录首个正文增量的耗时；重复调用只保留第一次。"""
        if self._first_token_ms is None:
            self._first_token_ms = int((time.perf_counter() - self._started_at) * 1000)

    async def end_ok(self, *, usage: dict[str, int | None] | None) -> None:
        await _finish_turn(
            self.trace_id,
            status=STATUS_OK,
            started_at=self._started_at,
            first_token_ms=self._first_token_ms,
            usage=usage,
            error_message=None,
        )

    async def end_error(self, exc: BaseException) -> None:
        await _finish_turn(
            self.trace_id,
            status=STATUS_ERROR,
            started_at=self._started_at,
            first_token_ms=self._first_token_ms,
            usage=None,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    async def end_stopped(self) -> None:
        await _finish_turn(
            self.trace_id,
            status=_STATUS_STOPPED,
            started_at=self._started_at,
            first_token_ms=self._first_token_ms,
            usage=None,
            error_message=None,
        )


async def start_chat_turn(
    *,
    session_id: str,
    agent_id: str,
    provider: str,
    model: str,
    user_input: str,
    channel: str = "",
) -> ChatTurnObservation:
    if session_id == "":
        raise ValueError("session_id 不能为空")
    if agent_id == "":
        raise ValueError("agent_id 不能为空")
    if provider == "":
        raise ValueError("provider 不能为空")
    if model == "":
        raise ValueError("model 不能为空")

    trace_id = str(uuid4())
    request_id = get_request_id()
    row = ObservationTurn(
        id=trace_id,
        session_id=session_id,
        agent_id=agent_id,
        provider=provider,
        model=model,
        channel=channel,
        status=_STATUS_RUNNING,
        request_id=request_id if request_id != "-" else "",
        input_summary=_summary(user_input, _INPUT_SUMMARY_MAX),
    )
    # 独立会话落库，避免与流式回合共用 db 互相 commit 干扰
    async with SessionLocal() as db:
        db.add(row)
        await db.commit()
    logger.info(
        "观测回合开始 trace_id=%s session_id=%s agent_id=%s model=%s",
        trace_id,
        session_id,
        agent_id,
        model,
    )
    return ChatTurnObservation(trace_id=trace_id, _started_at=time.perf_counter())


def _turn_filters(
    *,
    q: str | None,
    session_id: str | None,
    status: str | None,
    agent_id: str | None,
    model: str | None,
    channel: str | None,
    started_from: datetime | None,
    started_to: datetime | None,
    min_duration_ms: int | None,
) -> list:
    """构造列表与统计共用的 WHERE 条件；非法入参直接报错。"""

    if status is not None and status not in (
        _STATUS_RUNNING,
        STATUS_OK,
        STATUS_ERROR,
        _STATUS_STOPPED,
    ):
        raise ValueError(f"status 取值非法: {status}")
    if session_id is not None and session_id == "":
        raise ValueError("session_id 不能为空字符串")
    if agent_id is not None and agent_id == "":
        raise ValueError("agent_id 不能为空字符串")
    if model is not None and model == "":
        raise ValueError("model 不能为空字符串")
    if channel is not None and channel == "":
        raise ValueError("channel 不能为空字符串")
    if q is not None and q == "":
        raise ValueError("q 不能为空字符串")
    if min_duration_ms is not None and min_duration_ms < 0:
        raise ValueError("min_duration_ms 不能为负")
    if started_from is not None and started_to is not None and started_from > started_to:
        raise ValueError("started_from 不能晚于 started_to")

    filters = []
    if q is not None:
        like = f"%{q}%"
        # 关键字同时匹配 trace_id / session_id / request_id，避免只能按会话精确查。
        filters.append(
            or_(
                ObservationTurn.id.ilike(like),
                ObservationTurn.session_id.ilike(like),
                ObservationTurn.request_id.ilike(like),
            )
        )
    if session_id is not None:
        filters.append(ObservationTurn.session_id == session_id)
    if status is not None:
        filters.append(ObservationTurn.status == status)
    if agent_id is not None:
        filters.append(ObservationTurn.agent_id == agent_id)
    if model is not None:
        filters.append(ObservationTurn.model == model)
    if channel is not None:
        filters.append(ObservationTurn.channel == channel)
    if started_from is not None:
        filters.append(ObservationTurn.started_at >= started_from)
    if started_to is not None:
        filters.append(ObservationTurn.started_at <= started_to)
    if min_duration_ms is not None:
        filters.append(ObservationTurn.duration_ms >= min_duration_ms)
    return filters


async def list_turns(
    db: AsyncSession,
    *,
    q: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    channel: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    min_duration_ms: int | None = None,
    sort_by: str = "started_at",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ObservationTurn], int]:
    if limit < 1 or limit > 200:
        raise ValueError("limit 必须在 1..200")
    if offset < 0:
        raise ValueError("offset 不能为负")
    if sort_by not in ("started_at", "duration_ms"):
        raise ValueError(f"sort_by 取值非法: {sort_by}")
    if sort_order not in ("asc", "desc"):
        raise ValueError(f"sort_order 取值非法: {sort_order}")

    filters = _turn_filters(
        q=q,
        session_id=session_id,
        status=status,
        agent_id=agent_id,
        model=model,
        channel=channel,
        started_from=started_from,
        started_to=started_to,
        min_duration_ms=min_duration_ms,
    )
    count_stmt = select(func.count()).select_from(ObservationTurn)
    sort_column = (
        ObservationTurn.duration_ms if sort_by == "duration_ms" else ObservationTurn.started_at
    )
    ordered = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    if sort_by == "duration_ms":
        # 进行中的回合没有耗时，无论升降序都排在最后
        order_by = [
            ObservationTurn.duration_ms.is_(None),
            ordered,
            ObservationTurn.started_at.desc(),
            ObservationTurn.id.desc(),
        ]
    else:
        # 同秒记录的次序固定下来，避免分页时来回漂移
        order_by = [ordered, ObservationTurn.id.desc()]
    list_stmt = (
        select(ObservationTurn)
        .order_by(*order_by)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = int(await db.scalar(count_stmt) or 0)
    rows = list((await db.scalars(list_stmt)).all())
    return rows, total


async def summarize_turns(
    db: AsyncSession,
    *,
    q: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    channel: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    min_duration_ms: int | None = None,
) -> dict:
    """按同一组筛选条件汇总：失败数、平均/P95 延迟、tokens 总量。"""

    filters = _turn_filters(
        q=q,
        session_id=session_id,
        status=status,
        agent_id=agent_id,
        model=model,
        channel=channel,
        started_from=started_from,
        started_to=started_to,
        min_duration_ms=min_duration_ms,
    )
    from app.db import engine

    use_percentile_cont = engine.dialect.name == "postgresql"
    stmt = select(
        func.count().label("total"),
        func.count().filter(ObservationTurn.status == STATUS_ERROR).label("error_count"),
        func.count().filter(ObservationTurn.status == _STATUS_RUNNING).label("running_count"),
        func.avg(ObservationTurn.duration_ms).label("avg_duration_ms"),
        func.avg(ObservationTurn.first_token_ms).label("avg_first_token_ms"),
        func.coalesce(func.sum(ObservationTurn.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(ObservationTurn.tool_call_count), 0).label("tool_call_total"),
        func.coalesce(func.sum(ObservationTurn.skill_call_count), 0).label("skill_call_total"),
    ).select_from(ObservationTurn)
    if use_percentile_cont:
        # PG：有序集聚合直接算连续分位数
        stmt = stmt.add_columns(
            func.percentile_cont(0.95)
            .within_group(ObservationTurn.duration_ms.asc())
            .label("p95_duration_ms"),
            func.percentile_cont(0.95)
            .within_group(ObservationTurn.first_token_ms.asc())
            .label("p95_first_token_ms"),
        )
    if filters:
        stmt = stmt.where(*filters)
    row = (await db.execute(stmt)).one()
    avg_duration_ms = row.avg_duration_ms
    if use_percentile_cont:
        p95_duration_ms = row.p95_duration_ms
        p95_first_token_ms = row.p95_first_token_ms
    else:
        # SQLite 没有 percentile_cont：按最近秩取 P95（0 起，第 ceil(0.95*N)-1 位）
        total = int(row.total or 0)
        if total == 0:
            p95_duration_ms = None
        else:
            offset = max(0, math.ceil(0.95 * total) - 1)
            p95_stmt = (
                select(ObservationTurn.duration_ms)
                .order_by(ObservationTurn.duration_ms.asc())
                .limit(1)
                .offset(offset)
            )
            if filters:
                p95_stmt = p95_stmt.where(*filters)
            p95_duration_ms = await db.scalar(p95_stmt)
        # 未产出正文的回合 first_token_ms 为空，不参与首字延迟统计
        first_token_total = int(
            await db.scalar(
                select(func.count())
                .select_from(ObservationTurn)
                .where(ObservationTurn.first_token_ms.is_not(None), *filters)
            )
            or 0
        )
        if first_token_total == 0:
            p95_first_token_ms = None
        else:
            offset = max(0, math.ceil(0.95 * first_token_total) - 1)
            ttft_stmt = (
                select(ObservationTurn.first_token_ms)
                .where(ObservationTurn.first_token_ms.is_not(None))
                .order_by(ObservationTurn.first_token_ms.asc())
                .limit(1)
                .offset(offset)
            )
            if filters:
                ttft_stmt = ttft_stmt.where(*filters)
            p95_first_token_ms = await db.scalar(ttft_stmt)
    return {
        "total": int(row.total),
        "error_count": int(row.error_count),
        "running_count": int(row.running_count),
        "avg_duration_ms": int(round(float(avg_duration_ms))) if avg_duration_ms is not None else None,
        "p95_duration_ms": int(round(float(p95_duration_ms))) if p95_duration_ms is not None else None,
        "avg_first_token_ms": (
            int(round(float(row.avg_first_token_ms)))
            if row.avg_first_token_ms is not None
            else None
        ),
        "p95_first_token_ms": (
            int(round(float(p95_first_token_ms))) if p95_first_token_ms is not None else None
        ),
        "total_tokens": int(row.total_tokens),
        "tool_call_total": int(row.tool_call_total),
        "skill_call_total": int(row.skill_call_total),
    }


async def list_facets(db: AsyncSession) -> dict:
    """筛选项候选值：出现过的助理、模型与发布渠道。"""

    agents = (await db.scalars(
        select(ObservationTurn.agent_id).distinct().order_by(ObservationTurn.agent_id)
    )).all()
    models = (await db.scalars(
        select(ObservationTurn.model).distinct().order_by(ObservationTurn.model)
    )).all()
    channels = (await db.scalars(
        select(ObservationTurn.channel).distinct().order_by(ObservationTurn.channel)
    )).all()
    return {
        "agents": [a for a in agents if a != ""],
        "models": [m for m in models if m != ""],
        "channels": [c for c in channels if c != ""],
    }


async def record_tool_start(
    *, trace_id: str, tool_call_id: str, name: str, arguments: str
) -> str:
    """登记一次工具/技能调用开始，返回该调用行 id。"""

    if trace_id == "":
        raise ValueError("trace_id 不能为空")
    if tool_call_id == "":
        raise ValueError("tool_call_id 不能为空")
    if name == "":
        raise ValueError("工具名不能为空")

    kind, detail = classify_tool(name)
    # 技能调用把实参里的技能名记到 detail，便于排查到底加载了哪个技能
    if kind == "skill" and arguments != "":
        parsed = json.loads(arguments)
        if isinstance(parsed, dict):
            for key in ("skill_name", "name"):
                skill_name = parsed.get(key)
                if isinstance(skill_name, str) and skill_name != "":
                    detail = skill_name
                    break

    row_id = str(uuid4())
    row = ObservationToolCall(
        id=row_id,
        trace_id=trace_id,
        tool_call_id=tool_call_id,
        name=name,
        kind=kind,
        detail=detail,
        status=_STATUS_RUNNING,
        input_chars=len(arguments),
    )
    async with SessionLocal() as db:
        db.add(row)
        await db.commit()
    logger.info(
        "观测工具调用开始 trace_id=%s tool_call_id=%s name=%s kind=%s detail=%s input_chars=%s",
        trace_id,
        tool_call_id,
        name,
        kind,
        detail,
        len(arguments),
    )
    return row_id


async def record_tool_end(
    *,
    trace_id: str,
    tool_call_id: str,
    name: str,
    status: str,
    duration_ms: int,
    error: str | None,
    result_chars: int = 0,
) -> None:
    """结束一次工具/技能调用，并把计数累加到所属回合。"""

    if trace_id == "":
        raise ValueError("trace_id 不能为空")
    if status not in (STATUS_OK, STATUS_ERROR):
        raise ValueError(f"工具调用状态非法: {status}")
    if duration_ms < 0:
        raise ValueError("duration_ms 不能为负")

    async with SessionLocal() as db:
        row = await db.scalar(
            select(ObservationToolCall).where(
                ObservationToolCall.trace_id == trace_id,
                ObservationToolCall.tool_call_id == tool_call_id,
            )
        )
        if row is None:
            # 审批续跑不会重发 tool_start（确认已在上回合完成），在此补登记
            kind, detail = classify_tool(name)
            row = ObservationToolCall(
                id=str(uuid4()),
                trace_id=trace_id,
                tool_call_id=tool_call_id,
                name=name,
                kind=kind,
                detail=detail,
                status=_STATUS_RUNNING,
            )
            db.add(row)
        elif row.status != _STATUS_RUNNING:
            raise ValueError(f"工具调用已结束: {row.id} status={row.status}")
        row.status = status
        row.duration_ms = duration_ms
        row.error = error
        row.result_chars = result_chars
        turn = await db.get(ObservationTurn, trace_id)
        if turn is None:
            raise LookupError(f"观测记录不存在: {trace_id}")
        if row.kind == "skill":
            turn.skill_call_count = turn.skill_call_count + 1
        else:
            turn.tool_call_count = turn.tool_call_count + 1
        await db.commit()
    logger.info(
        "观测工具调用结束 trace_id=%s name=%s status=%s duration_ms=%s result_chars=%s",
        trace_id,
        name,
        status,
        duration_ms,
        result_chars,
    )


async def list_tool_calls(db: AsyncSession, trace_id: str) -> list[ObservationToolCall]:
    if trace_id == "":
        raise ValueError("trace_id 不能为空")
    rows = await db.scalars(
        select(ObservationToolCall)
        .where(ObservationToolCall.trace_id == trace_id)
        .order_by(ObservationToolCall.started_at)
    )
    return list(rows.all())


def tool_call_to_wire(row: ObservationToolCall) -> dict:
    return {
        "tool_call_id": row.tool_call_id,
        "name": row.name,
        "kind": row.kind,
        "detail": row.detail,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "input_chars": row.input_chars,
        "result_chars": row.result_chars,
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
    }


async def get_turn(db: AsyncSession, trace_id: str) -> ObservationTurn:
    if trace_id == "":
        raise ValueError("trace_id 不能为空")
    row = await db.get(ObservationTurn, trace_id)
    if row is None:
        raise LookupError(f"观测记录不存在: {trace_id}")
    return row


def turn_to_wire(row: ObservationTurn) -> dict:
    return {
        "trace_id": row.id,
        "session_id": row.session_id,
        "agent_id": row.agent_id,
        "provider": row.provider,
        "model": row.model,
        # 空串 = 站内对话；非空 = 对外发布渠道名
        "channel": row.channel,
        "status": row.status,
        "request_id": row.request_id,
        "input_summary": row.input_summary,
        "error_message": row.error_message,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "duration_ms": row.duration_ms,
        "first_token_ms": row.first_token_ms,
        "tool_call_count": row.tool_call_count,
        "skill_call_count": row.skill_call_count,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
    }


async def _finish_turn(
    trace_id: str,
    *,
    status: str,
    started_at: float,
    first_token_ms: int | None,
    usage: dict[str, int | None] | None,
    error_message: str | None,
) -> None:
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    prompt_tokens, completion_tokens, total_tokens = _split_usage(usage)
    async with SessionLocal() as db:
        row = await db.get(ObservationTurn, trace_id)
        if row is None:
            raise LookupError(f"观测记录不存在: {trace_id}")
        row.status = status
        row.duration_ms = duration_ms
        row.first_token_ms = first_token_ms
        row.ended_at = datetime.now(timezone.utc)
        row.error_message = error_message
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.total_tokens = total_tokens
        await db.commit()
    logger.info(
        "观测回合结束 trace_id=%s status=%s duration_ms=%s tokens=%s",
        trace_id,
        status,
        duration_ms,
        total_tokens,
    )


def _summary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(truncated,len={len(text)})"


def _split_usage(
    usage: dict[str, int | None] | None,
) -> tuple[int | None, int | None, int | None]:
    """无有效 token 保持 null，禁止假 0。"""

    if usage is None:
        return None, None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
        total if isinstance(total, int) else None,
    )
