"""可观测 API：回合 Trace 列表、汇总与详情。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services.observability import observation_service
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["observability"])


class ObservationTurnOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    session_id: str
    agent_id: str
    provider: str
    model: str
    channel: str
    status: str
    request_id: str
    input_summary: str
    error_message: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
    first_token_ms: int | None
    tool_call_count: int
    skill_call_count: int
    started_at: str | None
    ended_at: str | None


class ObservationToolCallOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    name: str
    kind: str
    detail: str
    status: str
    duration_ms: int | None
    error: str | None
    started_at: str | None


class ObservationTurnDetailOut(ObservationTurnOut):
    tool_calls: list[ObservationToolCallOut]


class ObservationTurnListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ObservationTurnOut]
    total: int


class ObservationStatsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    error_count: int
    running_count: int
    avg_duration_ms: int | None
    p95_duration_ms: int | None
    avg_first_token_ms: int | None
    p95_first_token_ms: int | None
    total_tokens: int
    tool_call_total: int
    skill_call_total: int


class ObservationOverviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: ObservationStatsOut
    agents: list[str]
    models: list[str]
    channels: list[str]


@router.get("/observability/turns", response_model=ObservationTurnListOut)
async def list_observation_turns(
    q: str | None = Query(default=None, description="按输入摘要关键字模糊筛选"),
    session_id: str | None = Query(default=None, description="按会话 id 筛选；留空不过滤"),
    status: str | None = Query(default=None, description="按回合状态筛选，如 running/success/error"),
    agent_id: str | None = Query(default=None, description="按助理 id 筛选；留空不过滤"),
    model: str | None = Query(default=None, description="按模型名筛选；留空不过滤"),
    channel: str | None = Query(default=None, description="按渠道名筛选；留空不过滤"),
    started_from: datetime | None = Query(default=None, description="只返回开始时间不早于此值的回合"),
    started_to: datetime | None = Query(default=None, description="只返回开始时间不晚于此值的回合"),
    min_duration_ms: int | None = Query(default=None, ge=0, description="只返回耗时不小于该值（毫秒）的回合"),
    sort_by: str = Query(default="started_at", description="排序字段，如 started_at/duration_ms"),
    sort_order: str = Query(default="desc", description="排序方向：desc 倒序，asc 正序"),
    limit: int = Query(default=50, ge=1, le=200, description="每页条数，1-200"),
    offset: int = Query(default=0, ge=0, description="分页偏移量，从 0 开始"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationTurnListOut:
    """分页列出回合 Trace（含筛选与排序），total 为筛选后的总数。"""

    try:
        rows, total = await observation_service.list_turns(
            db,
            q=q,
            session_id=session_id,
            status=status,
            agent_id=agent_id,
            model=model,
            channel=channel,
            started_from=started_from,
            started_to=started_to,
            min_duration_ms=min_duration_ms,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ObservationTurnListOut(
        items=[ObservationTurnOut(**observation_service.turn_to_wire(r)) for r in rows],
        total=total,
    )


@router.get("/observability/overview", response_model=ObservationOverviewOut)
async def observation_overview(
    q: str | None = Query(default=None, description="按输入摘要关键字模糊筛选"),
    session_id: str | None = Query(default=None, description="按会话 id 筛选；留空不过滤"),
    status: str | None = Query(default=None, description="按回合状态筛选，如 running/success/error"),
    agent_id: str | None = Query(default=None, description="按助理 id 筛选；留空不过滤"),
    model: str | None = Query(default=None, description="按模型名筛选；留空不过滤"),
    channel: str | None = Query(default=None, description="按渠道名筛选；留空不过滤"),
    started_from: datetime | None = Query(default=None, description="只统计开始时间不早于此值的回合"),
    started_to: datetime | None = Query(default=None, description="只统计开始时间不晚于此值的回合"),
    min_duration_ms: int | None = Query(default=None, ge=0, description="只统计耗时不小于该值（毫秒）的回合"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationOverviewOut:
    """按当前筛选条件汇总回合统计指标，并给出可用的助理/模型/渠道筛选项。"""

    try:
        stats = await observation_service.summarize_turns(
            db,
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
        facets = await observation_service.list_facets(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ObservationOverviewOut(
        stats=ObservationStatsOut(**stats),
        agents=facets["agents"],
        models=facets["models"],
        channels=facets["channels"],
    )


@router.get("/observability/turns/{trace_id}", response_model=ObservationTurnDetailOut)
async def get_observation_turn(
    trace_id: str = Path(description="要获取详情的回合 trace id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationTurnDetailOut:
    """按 trace id 获取单个回合详情，含其下的工具调用明细。"""

    try:
        row = await observation_service.get_turn(db, trace_id)
        tool_calls = await observation_service.list_tool_calls(db, trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ObservationTurnDetailOut(
        **observation_service.turn_to_wire(row),
        # 逐条过 pydantic：出参契约（字段集合 / 类型）在服务层还是裸 dict
        tool_calls=[
            ObservationToolCallOut(**observation_service.tool_call_to_wire(t))
            for t in tool_calls
        ],
    )
