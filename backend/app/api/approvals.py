"""审批 API：对接前端 approvals.ts。"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth
from app.db import get_db
from app.services import approval_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["approvals"])


class RejectBody(BaseModel):
    reason: str


class ApproveBody(BaseModel):
    # 批准允许空说明
    reason: str = ""


class BatchDecideBody(BaseModel):
    ids: list[str]
    confirmed: bool
    # 批准可空；拒绝由服务层校验必填
    reason: str = ""


@router.get("/approvals")
async def list_approvals(
    status: str | None = None,
    q: str | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    sort_dir: str = Query("desc"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        items, total = await approval_service.list_approvals(
            db,
            status,
            q=q,
            created_from=created_from,
            created_to=created_to,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
        # 统计与列表共用搜索与时间条件，但覆盖全部状态，供顶部指标卡使用。
        counts = await approval_service.approval_status_counts(
            db,
            q=q,
            created_from=created_from,
            created_to=created_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": [approval_service.approval_to_wire(a) for a in items],
        "total": total,
        "counts": counts,
    }


@router.get("/approvals/count")
async def approvals_count(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    return {"pending": await approval_service.pending_count(db)}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        row = await approval_service.get_approval(db, approval_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return approval_service.approval_to_wire(row)


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    body: ApproveBody,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        row = await approval_service.approve(db, approval_id, reason=body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return approval_service.approval_to_wire(row)


@router.post("/approvals/batch")
async def batch_decide(
    body: BatchDecideBody,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    if len(body.ids) == 0:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    try:
        items = await approval_service.batch_decide(
            db, ids=body.ids, confirmed=body.confirmed, reason=body.reason
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"items": [approval_service.approval_to_wire(a) for a in items]}


@router.post("/approvals/{approval_id}/approve/stream")
async def approve_stream(
    approval_id: str,
    body: ApproveBody,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
):
    """批准并续跑：SSE 推送与聊天流同构的增量帧，前端可打字机渲染。"""

    async def gen():
        try:
            async for frame in approval_service.stream_decide(
                db,
                approval_id=approval_id,
                confirmed=True,
                reason=body.reason,
            ):
                yield {
                    "event": frame.event,
                    "data": json.dumps(frame.data, ensure_ascii=False),
                }
        except LookupError as exc:
            logger.exception("审批流式失败 approval_id=%s", approval_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        except ValueError as exc:
            logger.exception("审批流式冲突 approval_id=%s", approval_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
        except Exception as exc:
            logger.exception("审批流式异常 approval_id=%s", approval_id)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    return EventSourceResponse(gen(), sep="\n")


@router.post("/approvals/{approval_id}/reject")
async def reject(
    approval_id: str,
    body: RejectBody,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    if body.reason == "":
        raise HTTPException(status_code=400, detail="拒绝原因 reason 必填")
    try:
        await approval_service.reject(db, approval_id, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
