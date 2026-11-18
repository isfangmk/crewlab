"""跨会话记忆的管理 API（列表 / 新增 / 启停 / 删除）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_not_viewer
from app.db import get_db
from app.models.memory import MemoryItem
from app.services.knowledge import memory_service
from app.services.observability.audit_service import write_audit
from app.vector_store import VectorStoreUnavailable

router = APIRouter(tags=["memory"])


class MemoryCreateRequest(BaseModel):
    """新增记忆的请求体。"""

    content: str = Field(min_length=1, max_length=2000, description="要记住的事实或偏好，一句话一条，最长 2000 字")
    agent_id: str | None = Field(default=None, description="记忆归属的助理 id；留空为全局记忆，所有助理可见")


class MemoryUpdateRequest(BaseModel):
    """更新记忆的请求体。"""

    enabled: bool = Field(description="是否启用；停用后不再参与语义回忆")


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
    q: str | None = Query(default=None, description="按记忆正文关键字模糊筛选"),
    agent_id: str | None = Query(default=None, description="按归属助理 id 筛选；留空不过滤"),
    enabled: bool | None = Query(default=None, description="按启用状态筛选：true 仅启用、false 仅停用、留空全部"),
    sort_dir: str = Query(default="desc", description="排序方向：desc 最新在前，asc 最早在前"),
    limit: int = Query(default=20, description="每页条数，服务端分页上限"),
    offset: int = Query(default=0, description="分页偏移量，从 0 开始"),
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
    memory_id: str = Path(description="要更新的记忆 id（先通过列表获取）"),
    body: MemoryUpdateRequest = Body(description="要更新的字段"),
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
    memory_id: str = Path(description="要删除的记忆 id（先通过列表获取）"),
    db: AsyncSession = Depends(get_db),
    user: Any = Depends(require_not_viewer),
) -> dict:
    """删除一条记忆，不可恢复。"""
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
