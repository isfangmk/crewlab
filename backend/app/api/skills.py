"""技能目录 API（只读 + 热重载校验）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_auth, require_not_viewer
from app.services import skills_service
from app.services.auth_service import AuthUser

router = APIRouter(tags=["skills"])


@router.get("/skills")
async def list_skills(
    category: str | None = Query(None),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = skills_service.list_skill_summaries()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if category is not None:
        items = [i for i in items if i.get("category") == category]
    return {"items": items, "total": len(items)}


@router.get("/skills/{key}")
async def get_skill(
    key: str,
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return skills_service.get_skill_summary(key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/reload")
async def reload_skills(
    _user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """从磁盘重装技能目录；frontmatter 非法时显式失败。"""

    try:
        items = skills_service.reload_skill_summaries()
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}
