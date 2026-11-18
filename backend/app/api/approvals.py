"""审批 API：对接前端 approvals.ts。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services.chat import approval_service
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

logger = logging.getLogger(__name__)
router = APIRouter(tags=["approvals"])


class RejectBody(BaseModel):
    """拒绝审批的请求体。"""

    reason: str = Field(description="拒绝原因，必填，不能为空字符串")


class ApproveBody(BaseModel):
    """批准审批的请求体。"""

    # 批准允许空说明
    reason: str = Field(default="", description="批准说明，可留空")
    # 澄清提问（kind=user_input）的用户回答 {selected, values, text}；普通审批不带
    answer: dict | None = Field(default=None, description="澄清提问的用户回答，结构为 {selected, values, text}；普通审批留空")
    # 免审范围：仅审批闸门生效，不豁免澄清提问
    #   "tool" = 本次对话不再询问此工具；"all" = 本次对话不再询问所有工具；None = 仅本次
    trust_scope: Literal["tool", "all"] | None = Field(
        default=None,
        description="免审范围，仅对审批闸门生效、不豁免澄清提问：tool 本次对话不再询问此工具，all 本次对话不再询问所有工具，留空仅本次询问",
    )


class BatchDecideBody(BaseModel):
    """批量审批/拒绝的请求体。"""

    ids: list[str] = Field(description="要处理的审批 id 列表，不能为空")
    confirmed: bool = Field(description="是否批准：true 批量批准，false 批量拒绝")
    # 批准可空；拒绝由服务层校验必填
    reason: str = Field(default="", description="处理说明；批准可留空，拒绝时必填")


@router.get("/approvals")
async def list_approvals(
    status: str | None = Query(None, description="按状态筛选：pending 待审批 / approved 已批准 / executed 已执行 / rejected 已拒绝 / failed 失败 / expired 已过期；留空全部"),
    q: str | None = Query(None, description="按审批内容关键字模糊筛选；留空不过滤"),
    created_from: datetime | None = Query(None, description="创建时间下限（含），ISO 8601 格式；留空不限"),
    created_to: datetime | None = Query(None, description="创建时间上限（含），ISO 8601 格式；留空不限"),
    sort_dir: str = Query("desc", description="排序方向：desc 最新在前，asc 最早在前"),
    limit: int = Query(20, ge=1, le=200, description="每页条数，1~200"),
    offset: int = Query(0, ge=0, description="分页偏移量，从 0 开始"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """审批列表（管理页）：支持状态/关键字/时间筛选与服务端分页，并附各状态命中数。"""

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
    timeout_hours = await approval_service.get_approval_timeout_hours(db)
    return {
        "items": [approval_service.approval_to_wire(a, timeout_hours) for a in items],
        "total": total,
        "counts": counts,
    }


@router.get("/approvals/count")
async def approvals_count(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """统计当前待审批（pending）条数，供首页角标使用。"""
    return {"pending": await approval_service.pending_count(db)}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str = Path(description="要查看的审批 id"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """按 id 查看单条审批详情。"""
    try:
        row = await approval_service.get_approval(db, approval_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return approval_service.approval_to_wire(
        row, await approval_service.get_approval_timeout_hours(db)
    )


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str = Path(description="要批准的审批 id"),
    body: ApproveBody = Body(description="批准请求体，可附说明/澄清回答/免审范围"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """批准一条审批：非管理员不得批准升级（escalate）审批。"""
    try:
        row = await approval_service.get_approval(db, approval_id)
        approval_service.require_escalation_permission(row.payload, is_admin=user.role == "admin")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        row = await approval_service.approve(
            db,
            approval_id,
            reason=body.reason,
            answer=body.answer,
            trust_scope=body.trust_scope,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval.approve",
        resource_type="approval",
        resource_id=row.id,
        detail={"kind": row.kind, "reason": body.reason},
    )
    return approval_service.approval_to_wire(
        row, await approval_service.get_approval_timeout_hours(db)
    )


@router.post("/approvals/batch")
async def batch_decide(
    body: BatchDecideBody,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """批量批准或拒绝多条审批；非管理员不得批量批准升级（escalate）审批。"""
    if len(body.ids) == 0:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if body.confirmed:
        # 批量批准前逐单校验升级限制：非 admin 不得批 escalate 单，整批拒绝
        for rid in body.ids:
            try:
                row = await approval_service.get_approval(db, rid)
                approval_service.require_escalation_permission(
                    row.payload, is_admin=user.role == "admin"
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        items = await approval_service.batch_decide(
            db, ids=body.ids, confirmed=body.confirmed, reason=body.reason
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval.batch_approve" if body.confirmed else "approval.batch_reject",
        resource_type="approval",
        # resource_id 定长 128，拼多个 id 会溢出（4 个 UUID 就 146 字符）；
        # 批次不是单条资源，id 列表走 detail
        resource_id="",
        detail={"count": len(items), "ids": body.ids, "reason": body.reason},
    )
    timeout_hours = await approval_service.get_approval_timeout_hours(db)
    return {
        "items": [approval_service.approval_to_wire(a, timeout_hours) for a in items]
    }


@router.post("/approvals/{approval_id}/approve/stream")
async def approve_stream(
    approval_id: str = Path(description="要批准并续跑的审批 id"),
    body: ApproveBody = Body(description="批准请求体，可附说明/澄清回答/免审范围"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
):
    """批准并续跑：SSE 推送与聊天流同构的增量帧，前端可打字机渲染。"""

    try:
        row = await approval_service.get_approval(db, approval_id)
        approval_service.require_escalation_permission(row.payload, is_admin=user.role == "admin")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    async def gen():
        try:
            async for frame in approval_service.stream_decide(
                db,
                approval_id=approval_id,
                confirmed=True,
                reason=body.reason,
                answer=body.answer,
                trust_scope=body.trust_scope,
            ):
                yield {
                    "event": frame.event,
                    "data": json.dumps(frame.data, ensure_ascii=False),
                }
            await write_audit(
                actor_user_id=user.id,
                action="approval.approve",
                resource_type="approval",
                resource_id=approval_id,
                detail={"reason": body.reason, "mode": "stream"},
            )
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
    approval_id: str = Path(description="要拒绝的审批 id"),
    body: RejectBody = Body(description="拒绝请求体，reason 必填"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """拒绝一条审批，拒绝原因必填。"""
    if body.reason == "":
        raise HTTPException(status_code=400, detail="拒绝原因 reason 必填")
    try:
        await approval_service.reject(db, approval_id, body.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="approval.reject",
        resource_type="approval",
        resource_id=approval_id,
        detail={"reason": body.reason},
    )
    return {"ok": True}
