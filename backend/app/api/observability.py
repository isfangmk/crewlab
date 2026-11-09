"""可观测 API：回合 Trace 列表、汇总与详情。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import observation_service
from app.services.auth_service import AuthUser

router = APIRouter(tags=["observability"])


class ObservationTurnOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    session_id: str
    agent_id: str
    provider: str
    model: str
    status: str
    request_id: str
    input_summary: str
    error_message: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
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
    total_tokens: int
    tool_call_total: int
    skill_call_total: int


class ObservationOverviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: ObservationStatsOut
    agents: list[str]
    models: list[str]


@router.get("/observability/turns", response_model=ObservationTurnListOut)
async def list_observation_turns(
    q: str | None = Query(None),
    session_id: str | None = Query(None),
    status: str | None = Query(None),
    agent_id: str | None = Query(None),
    model: str | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    min_duration_ms: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationTurnListOut:
    try:
        rows, total = await observation_service.list_turns(
            db,
            q=q,
            session_id=session_id,
            status=status,
            agent_id=agent_id,
            model=model,
            started_from=started_from,
            started_to=started_to,
            min_duration_ms=min_duration_ms,
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
    q: str | None = Query(None),
    session_id: str | None = Query(None),
    status: str | None = Query(None),
    agent_id: str | None = Query(None),
    model: str | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    min_duration_ms: int | None = Query(None, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationOverviewOut:
    try:
        stats = await observation_service.summarize_turns(
            db,
            q=q,
            session_id=session_id,
            status=status,
            agent_id=agent_id,
            model=model,
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
    )


@router.get("/observability/turns/{trace_id}", response_model=ObservationTurnDetailOut)
async def get_observation_turn(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> ObservationTurnDetailOut:
    try:
        row = await observation_service.get_turn(db, trace_id)
        tool_calls = await observation_service.list_tool_calls(db, trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ObservationTurnDetailOut(
        **observation_service.turn_to_wire(row),
        tool_calls=[observation_service.tool_call_to_wire(t) for t in tool_calls],
    )
