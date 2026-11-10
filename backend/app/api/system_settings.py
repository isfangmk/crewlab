"""system-settings：LLM 展平视图 + agent 键值设置 + 脱敏配置快照导出。"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.services import app_settings_service, llm_admin
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.redaction import MASK

router = APIRouter(tags=["system-settings"])

# 快照包含这些分类；llm 走 llm_admin 的展平视图。
_SNAPSHOT_CATEGORIES = ("agent", "observability", "knowledge")


class SettingPutIn(BaseModel):
    value: str
    sensitive: bool | None = None


@router.get("/system-settings")
async def list_settings(
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """读取设置：所有登录用户都需要 observability.timezone 来渲染时间戳。

    写入、揭示密钥、导出快照才收口到管理员。llm 的 api_key 项只返回 ********。
    """
    if category is None:
        raise HTTPException(status_code=400, detail="必须指定 category")
    if category == "llm":
        items = await llm_admin.list_llm_settings(db)
        return {"items": items, "total": len(items)}
    if category in {"agent", "observability", "knowledge"}:
        try:
            items = await app_settings_service.list_category(db, category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items, "total": len(items)}
    raise HTTPException(status_code=501, detail=f"category={category} 未实现")


@router.get("/system-settings/export")
async def export_settings(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> Response:
    """导出当前配置快照：密钥类值一律遮蔽，可直接作为合规附件。"""

    rows: list[dict] = []
    for category in _SNAPSHOT_CATEGORIES:
        rows.extend(await app_settings_service.list_category(db, category))
    rows.extend(await llm_admin.list_llm_settings(db))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["分类", "键", "值", "敏感"])
    for item in rows:
        sensitive = bool(item.get("sensitive"))
        value = str(item.get("value", ""))
        writer.writerow(
            [
                item.get("category", ""),
                item.get("key", ""),
                MASK if sensitive else value,
                "是" if sensitive else "否",
            ]
        )
    body = "\ufeff" + buf.getvalue()
    await write_audit(
        db,
        actor_user_id=user.id,
        action="setting.export",
        resource_type="setting",
        detail={"rows": len(rows), "categories": list(_SNAPSHOT_CATEGORIES) + ["llm"]},
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="settings-snapshot.csv"'},
    )


@router.put("/system-settings/{category}/{key}")
async def put_setting(
    category: str,
    key: str,
    body: SettingPutIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    try:
        result = await app_settings_service.put_setting(
            db,
            category=category,
            key=key,
            value=body.value,
            sensitive=body.sensitive,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="setting.update",
        resource_type="setting",
        resource_id=f"{category}.{key}",
        detail={"value": body.value, "sensitive": bool(body.sensitive)},
    )
    return result


@router.get("/system-settings/{category}/{key}/reveal")
async def reveal_setting(
    category: str,
    key: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
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
    # 揭示明文密钥属于敏感操作：只记录谁在何时看了哪一家，不落值。
    await write_audit(
        db,
        actor_user_id=user.id,
        action="setting.reveal",
        resource_type="setting",
        resource_id=f"{category}.{key}",
        detail={"provider": provider_id},
    )
    return {"value": value}
