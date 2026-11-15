"""跨会话记忆的管理 API（列表 / 新增 / 启停 / 删除）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_not_viewer
from app.db import get_db
from app.models.memory import MemoryItem
from app.services import memory_service
from app.services.audit_service import write_audit
from app.vector_store import VectorStoreUnavailable

router = APIRouter(tags=["memory"])


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    agent_id: str | None = None


class MemoryUpdateRequest(BaseModel):
    enabled: bool


def _memory_to_wire(row: MemoryItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content,
        "agent_id": row.scope_agent_id,
        "enabled": row.enabled,
        "source_session_id": row.source_session_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/memory")
async def list_memory(
    q: str | None = None,
    agent_id: str | None = None,
    enabled: bool | None = None,
    sort_dir: str = "desc",
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _user: Any = Depends(require_not_viewer),
) -> dict:
    """列记忆（管理页）；支持关键字、作用域、启停筛选与服务端分页。"""

    try:
        rows, total = await memory_service.list_memories(
            db,
            q=q,
            agent_id=agent_id,
            enabled=enabled,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [_memory_to_wire(r) for r in rows], "total": total}


@router.post("/memory")
async def create_memory(
    body: MemoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    """手工新增一条记忆（也可由对话内 remember_memory 工具保存）。"""

    try:
        item = await memory_service.remember(
            db,
            content=body.content,
            agent_id=body.agent_id,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # 审计只记外形：记忆正文可能含个人事实，不复制进审计详情
    await write_audit(
        actor_user_id=user.id,
        action="memory.create",
        resource_type="memory",
        resource_id=item.id,
        detail={"content_len": len(body.content), "agent_id": body.agent_id},
    )
    return _memory_to_wire(item)


@router.patch("/memory/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    """启用/停用一条记忆；停用后不再参与语义回忆。"""

    try:
        item = await memory_service.set_enabled(db, memory_id, body.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="memory.update",
        resource_type="memory",
        resource_id=memory_id,
        detail={"enabled": body.enabled},
    )
    return _memory_to_wire(item)


@router.delete("/memory/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    try:
        await memory_service.forget(db, memory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="memory.delete",
        resource_type="memory",
        resource_id=memory_id,
    )
    return {"ok": True}
