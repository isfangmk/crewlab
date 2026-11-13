"""沙盒执行记录 API：列表、详情、概览、手动试跑。

执行记录是「谁在沙盒里跑了什么、结果如何」的账本：
对话里的技能脚本、工作流里的代码节点、页面上的手动试跑都在这里。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import sandbox_query_service, sandbox_service
from app.services.auth_service import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sandbox"])


class SandboxExecutionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    source_ref: str
    session_id: str
    workflow_run_id: str
    language: str
    image: str
    network_enabled: bool
    code: str
    stdout: str
    stderr: str
    exit_code: int | None
    status: str
    error: str | None
    duration_ms: int
    started_at: str | None
    finished_at: str | None


class SandboxExecutionListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SandboxExecutionOut]
    total: int


class SandboxSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    by_source: dict[str, int]
    by_status: dict[str, int]
    avg_duration_ms: int


class SandboxRunInput(BaseModel):
    """手动试跑的入参。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20000)
    libraries: list[str] = Field(default_factory=list)


class SandboxRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    stdout: str
    stderr: str
    exit_code: int | None
    status: str
    error: str
    duration_ms: int


@router.get("/sandbox/executions", response_model=SandboxExecutionListOut)
async def list_sandbox_executions(
    q: str | None = Query(None),
    source: str | None = Query(None),
    status: str | None = Query(None),
    session_id: str | None = Query(None),
    workflow_run_id: str | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> SandboxExecutionListOut:
    try:
        rows, total = await sandbox_query_service.list_executions(
            db,
            q=q,
            source=source,
            status=status,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            started_from=started_from,
            started_to=started_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SandboxExecutionListOut(
        items=[
            SandboxExecutionOut(**sandbox_service.execution_to_wire(row)) for row in rows
        ],
        total=total,
    )


@router.get("/sandbox/executions/{execution_id}", response_model=SandboxExecutionOut)
async def get_sandbox_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> SandboxExecutionOut:
    try:
        row = await sandbox_query_service.get_execution(db, execution_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SandboxExecutionOut(**sandbox_service.execution_to_wire(row))


@router.get("/sandbox/summary", response_model=SandboxSummaryOut)
async def sandbox_summary(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> SandboxSummaryOut:
    return SandboxSummaryOut(**await sandbox_query_service.summarize(db))


@router.post("/sandbox/run", response_model=SandboxRunOut)
async def run_sandbox_code(
    payload: SandboxRunInput,
    _user: AuthUser = Depends(require_auth),
) -> SandboxRunOut:
    """手动跑一段代码：用于验证沙盒环境是否正常。"""

    try:
        result = await sandbox_service.run_code(
            code=payload.code,
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_MANUAL,
                source_ref="manual",
            ),
            libraries=payload.libraries,
        )
    except sandbox_service.SandboxUnavailable as exc:
        # 沙盒不可用要明确告诉前端：让它显示「环境不可用」，
        # 而不是笼统的 500，让人以为是自己的代码写错了。
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SandboxRunOut(
        execution_id=result.execution_id,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        status=result.status,
        error=result.error,
        duration_ms=result.duration_ms,
    )
