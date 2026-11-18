"""@ 提及检索 API：供输入框 @ 弹窗使用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services.chat.mention_service import search_mentions
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["mentions"])


@router.get("/aiops/mentions/search")
async def search(
    q: str = Query(default="", max_length=200, description="检索关键词，模糊匹配标题/名称；留空返回该类型全部"),
    type: str | None = Query(
        default=None,
        description="限定提及类型：file 文档 / server MCP 服务 / skill 技能 / workflow 工作流 / agent 助理 / session 会话；留空检索全部类型",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="每类最多返回条数，范围 1～50"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """按关键词检索可提及对象（文档 / MCP Server / 技能 / 工作流 / 助理 / 会话）。"""

    try:
        items = await search_mentions(
            db,
            q=q,
            mention_type=type,
            limit=limit,
            owner_user_id=user.id,
            is_admin=user.role == "admin",
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}
