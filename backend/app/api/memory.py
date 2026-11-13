"""跨会话记忆的管理 API（列表 / 新增 / 删除）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services import memory_service

router = APIRouter(tags=["memory"])


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    agent_id: str | None = None


@router.get("/memory")
async def list_memory(
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """列记忆（管理页）；agent_id 缺省列全部。"""

    rows = await memory_service.list_memories(db, agent_id=agent_id)
    return {
        "items": [
            {
                "id": r.id,
                "content": r.content,
                "agent_id": r.scope_agent_id,
                "enabled": r.enabled,
                "source_session_id": r.source_session_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


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
            created_by=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": item.id,
        "content": item.content,
        "agent_id": item.scope_agent_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.delete("/memory/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: Any = Depends(require_not_viewer),
) -> dict:
    try:
        await memory_service.forget(db, memory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
