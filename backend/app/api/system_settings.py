"""system-settings：LLM 展平视图 + agent 键值设置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import app_settings_service, llm_admin

router = APIRouter(tags=["system-settings"])


class SettingPutIn(BaseModel):
    value: str
    sensitive: bool | None = None


@router.get("/system-settings")
async def list_settings(
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    if category is None:
        raise HTTPException(status_code=400, detail="必须指定 category")
    if category == "llm":
        items = await llm_admin.list_llm_settings(db)
        return {"items": items, "total": len(items)}
    if category in {"agent", "observability"}:
        try:
            items = await app_settings_service.list_category(db, category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items, "total": len(items)}
    raise HTTPException(status_code=501, detail=f"category={category} 未实现")


@router.put("/system-settings/{category}/{key}")
async def put_setting(
    category: str,
    key: str,
    body: SettingPutIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        return await app_settings_service.put_setting(
            db,
            category=category,
            key=key,
            value=body.value,
            sensitive=body.sensitive,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/system-settings/{category}/{key}/reveal")
async def reveal_setting(
    category: str,
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    if category != "llm":
        raise HTTPException(status_code=501, detail=f"category={category} 未实现")
    if not key.endswith("_api_key"):
        raise HTTPException(status_code=400, detail="仅支持揭示 *_api_key")
    provider_id = key[: -len("_api_key")]
    if provider_id == "":
        raise HTTPException(status_code=400, detail="非法 key")
    try:
        value = await llm_admin.reveal_api_key(db, provider_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"value": value}
