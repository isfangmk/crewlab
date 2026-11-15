"""用量与成本看板：observation_turns 聚合 + 手动单价估算（只读）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services.observability import usage_service
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["usage"])


class UsageRowOut(BaseModel):
    """一个分组（日 / 模型 / 助理）的用量与成本。"""

    model_config = ConfigDict(extra="forbid")

    # 分组键：日期（YYYY-MM-DD）/ 模型名 / 助理 id
    key: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_avg_ms: float | None
    # 缺手动单价时为 None（未计价），不按 0 计
    cost: float | None
    unpriced_turns: int


class UsageStatsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_avg_ms: float | None
    cost: float | None
    unpriced_turns: int
    unpriced_tokens: int


class UsageFacetsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str]
    agents: list[str]
    channels: list[str]


class UsageSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: UsageStatsOut
    by_day: list[UsageRowOut]
    by_model: list[UsageRowOut]
    by_agent: list[UsageRowOut]
    # 筛选下拉候选值：当前结果集里出现过的维度值
    facets: UsageFacetsOut
    # 日 token 软限额（空 = 不限）；超限只提醒不拦截
    soft_limit_tokens: int | None
    today_tokens: int


@router.get("/usage/summary")
async def usage_summary(
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    agent_id: str | None = Query(None),
    model: str | None = Query(None),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> UsageSummaryOut:
    try:
        result = await usage_service.usage_summary(
            db,
            started_from=started_from,
            started_to=started_to,
            agent_id=agent_id,
            model=model,
            channel=channel,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UsageSummaryOut(**result)
