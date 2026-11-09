"""技能目录 API（只读）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_auth
from app.services import skills_service

router = APIRouter(tags=["skills"])


@router.get("/skills")
async def list_skills(
    category: str | None = Query(None),
    _user: str = Depends(require_auth),
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
    _user: str = Depends(require_auth),
) -> dict:
    try:
        return skills_service.get_skill_summary(key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
