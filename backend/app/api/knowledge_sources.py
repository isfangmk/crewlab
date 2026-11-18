"""外部知识源管理 API：接入 URL / Git 来源并触发后台同步。

读接口对已登录用户开放，写接口要求非只读角色；同步动作只负责登记状态
并启动后台任务，实际抓取与嵌入在 knowledge_sync 中完成。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.models.knowledge import KnowledgeDoc
from app.services.knowledge import knowledge_source_service, knowledge_sync
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["knowledge"])


class SourceCreateRequest(BaseModel):
    """新建外部知识源的 request body。"""

    name: str = Field(min_length=1, max_length=128, description="来源名称，1-128 字符，须唯一；同名会被拒绝")
    kind: Literal["url", "git"] = Field(description="来源类型：url 网址，git 仓库")
    location: str = Field(min_length=1, max_length=2048, description="来源地址：url 为 http(s) 网址，git 为 http(s)/ssh/file 地址或 git@ 形式；最长 2048 字符，不能含空白")
    branch: str = Field(default="", max_length=128, description="git 来源要同步的分支名；url 来源会忽略并置空，最长 128 字符")
    path_prefix: str = Field(default="", max_length=512, description="git 来源只同步仓库内的子目录，相对路径；url 来源会忽略并置空，最长 512 字符")
    knowledge_path: str = Field(default="", max_length=512, description="同步文档归入的知识库目录，相对路径；留空默认「外部源/来源名称」，最长 512 字符")
    enabled: bool = Field(default=True, description="是否启用该来源；停用后不再参与同步")


class SourceUpdateRequest(BaseModel):
    """更新外部知识源的 request body；未传字段保持原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=128, description="新来源名称，1-128 字符，须唯一；留空不改")
    kind: Literal["url", "git"] | None = Field(default=None, description="来源类型：url 网址，git 仓库；留空不改")
    location: str | None = Field(default=None, min_length=1, max_length=2048, description="新来源地址：url 为 http(s) 网址，git 为 http(s)/ssh/file 地址或 git@ 形式；最长 2048 字符；留空不改")
    branch: str | None = Field(default=None, max_length=128, description="git 来源要同步的分支名，最长 128 字符；留空不改")
    path_prefix: str | None = Field(default=None, max_length=512, description="git 来源同步的仓库子目录，相对路径，最长 512 字符；留空不改（url 来源会置空）")
    knowledge_path: str | None = Field(default=None, max_length=512, description="同步文档归入的知识库目录，相对路径，最长 512 字符；留空不改")
    enabled: bool | None = Field(default=None, description="是否启用该来源；留空不改")


async def _doc_count(db: AsyncSession, source_id: str) -> int:
    count = await db.scalar(
        select(func.count(KnowledgeDoc.id)).where(
            KnowledgeDoc.external_source_id == source_id
        )
    )
    return int(count or 0)


@router.get("/knowledge/sources")
async def list_sources(
    q: str | None = Query(default=None, max_length=200, description="按来源名称模糊筛选，最长 200 字；留空不过滤"),
    kind: str | None = Query(default=None, description="按来源类型筛选：url 网址、git 仓库；留空不过滤"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出全部外部知识源（含同步状态与文档数），支持名称与类型筛选。"""

    try:
        items = await knowledge_source_service.list_sources(db, q=q, kind=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.post("/knowledge/sources")
async def create_source(
    body: SourceCreateRequest = Body(description="要新建的外部知识源配置"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """登记一个新的外部知识源，创建后需再触发同步才会抓取文档。"""

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
    source_id: str = Path(description="要更新的外部知识源 id（先通过列表获取）"),
    body: SourceUpdateRequest = Body(description="要更新的字段，未传字段保持原值"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按 id 更新外部知识源配置，未传字段保持原值。"""

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
    source_id: str = Path(description="要删除的外部知识源 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """删除外部知识源及其已同步的文档；同步中无法删除。"""

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
    source_id: str = Path(description="要触发同步的外部知识源 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """触发外部知识源后台同步：登记同步中状态并启动抓取任务。"""

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
