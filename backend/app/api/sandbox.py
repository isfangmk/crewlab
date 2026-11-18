"""沙盒执行记录 API：列表、详情、概览、手动试跑。

执行记录是「谁在沙盒里跑了什么、结果如何」的账本：
对话里的技能脚本、工作流里的代码节点、页面上的手动试跑都在这里。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser
from app.services.sandbox import sandbox_query_service, sandbox_service

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

    code: str = Field(min_length=1, max_length=20000, description="要执行的代码，1-20000 字")
    libraries: list[str] = Field(default_factory=list, description="执行前需要预装的第三方库列表")


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
    q: str | None = Query(default=None, description="按代码或错误关键字模糊筛选"),
    source: str | None = Query(default=None, description="按来源筛选，如 chat/workflow/manual"),
    status: str | None = Query(default=None, description="按执行状态筛选，如 success/error"),
    session_id: str | None = Query(default=None, description="按会话 id 筛选；留空不过滤"),
    workflow_run_id: str | None = Query(default=None, description="按工作流运行 id 筛选；留空不过滤"),
    started_from: datetime | None = Query(default=None, description="只返回开始时间不早于此值的记录"),
    started_to: datetime | None = Query(default=None, description="只返回开始时间不晚于此值的记录"),
    sort_by: str = Query(default="started_at", description="排序字段，如 started_at/duration_ms"),
    sort_order: str = Query(default="desc", description="排序方向：desc 倒序，asc 正序"),
    limit: int = Query(default=50, ge=1, le=200, description="每页条数，1-200"),
    offset: int = Query(default=0, ge=0, description="分页偏移量，从 0 开始"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> SandboxExecutionListOut:
    """分页列出沙盒执行记录（含筛选与排序），total 为筛选后的总数。"""

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
            sort_by=sort_by,
            sort_order=sort_order,
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
    execution_id: str = Path(description="要获取详情的沙盒执行记录 id"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> SandboxExecutionOut:
    """按 id 获取单条沙盒执行记录详情（含代码与标准输出/错误）。"""

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
    """汇总沙盒执行记录：总数、按来源/状态分布与平均耗时。"""

    return SandboxSummaryOut(**await sandbox_query_service.summarize(db))


@router.post("/sandbox/run", response_model=SandboxRunOut)
async def run_sandbox_code(
    payload: SandboxRunInput,
    _db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
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
    # 审计只记执行外形：代码与输出不落审计详情（本体在执行记录里）
    await write_audit(
        actor_user_id=user.id,
        action="sandbox.run",
        resource_type="sandbox_execution",
        resource_id=result.execution_id,
        detail={
            "status": result.status,
            "exit_code": result.exit_code,
            "code_len": len(payload.code),
        },
    )
    return SandboxRunOut(
        execution_id=result.execution_id,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        status=result.status,
        error=result.error,
        duration_ms=result.duration_ms,
    )
