"""system-settings：LLM 展平视图 + agent 键值设置 + 脱敏配置快照导出。"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.services.observability.audit_service import write_audit
from app.services.observability.redaction import MASK
from app.services.platform import app_settings_service, llm_admin
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["system-settings"])

# 快照包含这些分类；llm 走 llm_admin 的展平视图。
_SNAPSHOT_CATEGORIES = ("agent", "observability", "knowledge", "usage")


class SettingPutIn(BaseModel):
    """写入一项设置的请求体。"""

    value: str = Field(description="要写入的设置值，字符串形式")
    sensitive: bool | None = Field(default=None, description="是否敏感项：当前设置不允许 sensitive=true（传 true 会报 400）；留空或 false 按普通项存储")


@router.get("/system-settings")
async def list_settings(
    category: str | None = Query(None, description="设置分类：llm / agent / observability / knowledge / usage；必填，留空报 400"),
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
    if category in {"agent", "observability", "knowledge", "usage"}:
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
    category: str = Path(description="设置分类：agent / observability / knowledge / usage"),
    key: str = Path(description="要写入的设置键名"),
    body: SettingPutIn = Body(description="要写入的值及敏感标记"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员写入一项设置（agent/observability/knowledge/usage 分类）；成功返回更新结果。"""
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
        actor_user_id=user.id,
        action="setting.update",
        resource_type="setting",
        resource_id=f"{category}.{key}",
        detail={"value": body.value, "sensitive": bool(body.sensitive)},
    )
    return result


@router.get("/system-settings/{category}/{key}/reveal")
async def reveal_setting(
    category: str = Path(description="设置分类，仅支持 llm"),
    key: str = Path(description="要揭示的设置键，仅支持以 _api_key 结尾，如 {provider_id}_api_key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员揭示一项 LLM API 密钥明文（仅 llm 分类的 *_api_key）；敏感操作会记审计。"""
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
        actor_user_id=user.id,
        action="setting.reveal",
        resource_type="setting",
        resource_id=f"{category}.{key}",
        detail={"provider": provider_id},
    )
    return {"value": value}
