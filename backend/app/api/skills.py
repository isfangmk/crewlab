"""技能目录 API（只读目录 + 技能页直接执行内置工具）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_auth
from app.services import skills_service

router = APIRouter(tags=["skills"])


class SkillExecuteRequest(BaseModel):
    """技能执行请求：只允许该技能声明的参数名。"""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, object] = Field(default_factory=dict)


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


@router.post("/skills/{key}/execute")
async def execute_skill(
    key: str,
    body: SkillExecuteRequest,
    _user: str = Depends(require_auth),
) -> dict:
    """执行技能声明的内置工具。

    技能不存在 / 参数非法 / 工具不可执行 都显式失败。
    """

    try:
        return await skills_service.execute_skill(key, body.params)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
