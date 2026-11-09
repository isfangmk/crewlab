"""@ 提及检索 API：供输入框 @ 弹窗使用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services.mention_service import search_mentions

router = APIRouter(tags=["mentions"])


@router.get("/aiops/mentions/search")
async def search(
    q: str = Query("", max_length=200),
    type: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """按关键词检索可提及对象（知识库文档 / MCP Server）。"""

    try:
        items = await search_mentions(db, q=q, mention_type=type, limit=limit)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}
