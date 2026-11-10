"""审计日志查询与导出 API（管理员）。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_db
from app.models_orm.audit import AuditLog
from app.models_orm.user import User
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.redaction import redact_value

router = APIRouter(tags=["audit"])


class AuditItemOut(BaseModel):
    id: int
    actor_user_id: int | None
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str
    detail: dict
    request_id: str | None
    created_at: datetime


class AuditListOut(BaseModel):
    items: list[AuditItemOut]
    total: int
    actions: list[str]
    resource_types: list[str]


def _parse_detail(raw: str, audit_id: int) -> dict:
    """detail_json 必须是对象；损坏的记录显式失败，不返回空对象。"""

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"审计记录 {audit_id} 的 detail_json 非法: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"审计记录 {audit_id} 的 detail_json 必须是 JSON 对象")
    return value


def _build_filters(
    *,
    action: str | None,
    resource_type: str | None,
    resource_id: str | None,
    actor_user_id: int | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list:
    """列表与导出共用同一套筛选条件，避免两边口径漂移。"""

    filters = []
    if action is not None:
        filters.append(AuditLog.action == action)
    if resource_type is not None:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        filters.append(AuditLog.resource_id == resource_id)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if created_from is not None:
        filters.append(AuditLog.created_at >= created_from)
    if created_to is not None:
        filters.append(AuditLog.created_at <= created_to)
    return filters


@router.get("/audit", response_model=AuditListOut)
async def list_audit(
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    sort_dir: str = Query("desc"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> AuditListOut:
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="sort_dir 必须是 asc 或 desc")

    filters = _build_filters(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        created_from=created_from,
        created_to=created_to,
    )

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    order_time = AuditLog.created_at.asc() if sort_dir == "asc" else AuditLog.created_at.desc()
    order_id = AuditLog.id.asc() if sort_dir == "asc" else AuditLog.id.desc()
    rows = (
        await db.execute(
            select(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(*filters)
            .order_by(order_time, order_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    try:
        items = [
            AuditItemOut(
                id=row.id,
                actor_user_id=row.actor_user_id,
                actor_email=email,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                detail=_parse_detail(row.detail_json, row.id),
                request_id=row.request_id,
                created_at=row.created_at,
            )
            for row, email in rows
        ]
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    actions = list(await db.scalars(select(distinct(AuditLog.action)).order_by(AuditLog.action)))
    resource_types = list(
        await db.scalars(
            select(distinct(AuditLog.resource_type)).order_by(AuditLog.resource_type)
        )
    )
    return AuditListOut(
        items=items,
        total=int(total or 0),
        actions=actions,
        resource_types=resource_types,
    )


@router.get("/audit/export")
async def export_audit(
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    sort_dir: str = Query("desc"),
    limit: int = Query(5000, ge=1, le=50000),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> Response:
    """按当前筛选导出 CSV；恒为脱敏导出，不提供关闭开关。

    历史脏数据里可能存着明文密钥，脱敏不是可选项——出库口径必须唯一。
    """

    if sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="sort_dir 必须是 asc 或 desc")

    filters = _build_filters(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        created_from=created_from,
        created_to=created_to,
    )
    order_time = AuditLog.created_at.asc() if sort_dir == "asc" else AuditLog.created_at.desc()
    order_id = AuditLog.id.asc() if sort_dir == "asc" else AuditLog.id.desc()
    rows = (
        await db.execute(
            select(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(*filters)
            .order_by(order_time, order_id)
            .limit(limit)
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["时间", "操作者", "动作", "资源类型", "资源ID", "Request ID", "详情 JSON"])
    for row, email in rows:
        try:
            detail = _parse_detail(row.detail_json, row.id)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        payload = json.dumps(redact_value(detail), ensure_ascii=False)
        writer.writerow(
            [
                row.created_at.isoformat(),
                email or "",
                row.action,
                row.resource_type,
                row.resource_id,
                row.request_id or "",
                payload,
            ]
        )
    # 带 BOM：Windows 版 Excel 直接双击不会把中文显示成乱码。
    body = "\ufeff" + buf.getvalue()
    # 导出本身就是把审计数据带出系统，必须自己也被审计。
    await write_audit(
        db,
        actor_user_id=user.id,
        action="audit.export",
        resource_type="audit",
        detail={
            "rows": len(rows),
            "filters": {
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "actor_user_id": actor_user_id,
                "created_from": None if created_from is None else created_from.isoformat(),
                "created_to": None if created_to is None else created_to.isoformat(),
            },
        },
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
