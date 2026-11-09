"""审批 API：对接前端 approvals.ts。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/approvals")
async def list_approvals(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        items = await approval_service.list_approvals(db, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [approval_service.approval_to_wire(a) for a in items]}


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
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        row = await approval_service.approve(db, approval_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return approval_service.approval_to_wire(row)


@router.post("/approvals/{approval_id}/approve/stream")
async def approve_stream(
    approval_id: str,
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
                reason=None,
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
    try:
        await approval_service.reject(db, approval_id, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
