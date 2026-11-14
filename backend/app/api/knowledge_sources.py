"""外部知识源管理 API：接入 URL / Git 来源并触发后台同步。

读接口对已登录用户开放，写接口要求非只读角色；同步动作只负责登记状态
并启动后台任务，实际抓取与嵌入在 knowledge_sync 中完成。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.models_orm.knowledge import KnowledgeDoc
from app.services import knowledge_source_service, knowledge_sync
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

router = APIRouter(tags=["knowledge"])


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["url", "git"]
    location: str = Field(min_length=1, max_length=2048)
    branch: str = Field(default="", max_length=128)
    path_prefix: str = Field(default="", max_length=512)
    knowledge_path: str = Field(default="", max_length=512)
    enabled: bool = True


class SourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal["url", "git"] | None = None
    location: str | None = Field(default=None, min_length=1, max_length=2048)
    branch: str | None = Field(default=None, max_length=128)
    path_prefix: str | None = Field(default=None, max_length=512)
    knowledge_path: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


async def _doc_count(db: AsyncSession, source_id: str) -> int:
    count = await db.scalar(
        select(func.count(KnowledgeDoc.id)).where(
            KnowledgeDoc.external_source_id == source_id
        )
    )
    return int(count or 0)


@router.get("/knowledge/sources")
async def list_sources(
    q: str | None = Query(None, max_length=200),
    kind: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await knowledge_source_service.list_sources(db, q=q, kind=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.post("/knowledge/sources")
async def create_source(
    body: SourceCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await knowledge_source_service.create_source(
            db,
            name=body.name,
            kind=body.kind,
            location=body.location,
            branch=body.branch,
            path_prefix=body.path_prefix,
            knowledge_path=body.knowledge_path,
            enabled=body.enabled,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.source_create",
        resource_type="knowledge_source",
        resource_id=row.id,
        detail={"name": row.name, "kind": row.kind, "location": row.location},
    )
    return knowledge_source_service.source_to_wire(row, 0)


@router.patch("/knowledge/sources/{source_id}")
async def update_source(
    source_id: str,
    body: SourceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await knowledge_source_service.update_source(
            db,
            source_id,
            name=body.name,
            kind=body.kind,
            location=body.location,
            branch=body.branch,
            path_prefix=body.path_prefix,
            knowledge_path=body.knowledge_path,
            enabled=body.enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.source_update",
        resource_type="knowledge_source",
        resource_id=row.id,
        detail={"name": row.name, "kind": row.kind},
    )
    return knowledge_source_service.source_to_wire(
        row, await _doc_count(db, row.id)
    )


@router.delete("/knowledge/sources/{source_id}")
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await knowledge_source_service.get_source(db, source_id)
        name = row.name
        await knowledge_source_service.delete_source(db, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.source_delete",
        resource_type="knowledge_source",
        resource_id=source_id,
        detail={"name": name},
    )
    return {"ok": True}


@router.post("/knowledge/sources/{source_id}/sync")
async def sync_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await knowledge_source_service.mark_syncing(db, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    knowledge_sync.start_sync(source_id)
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.source_sync",
        resource_type="knowledge_source",
        resource_id=source_id,
        detail={"name": row.name, "kind": row.kind},
    )
    return knowledge_source_service.source_to_wire(
        row, await _doc_count(db, source_id)
    )
