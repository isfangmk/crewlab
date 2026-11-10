"""技能目录 API：内置技能只读，用户技能可增删改 + zip 导入导出。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.schemas.skill import SkillPayloadIn
from app.services import skill_bundle, skills_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.skill_bundle import RISK_LEVELS, SkillPayload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


def _payload_from_in(body: SkillPayloadIn) -> SkillPayload:
    metadata: dict[str, Any] = {}
    if body.tools:
        metadata["adk_additional_tools"] = list(body.tools)
    if body.inject_state:
        metadata["adk_inject_state"] = True
    return SkillPayload(
        key=body.key,
        description=body.description,
        instructions=body.instructions,
        license=body.license,
        compatibility=body.compatibility,
        preapproved_tools=body.preapproved_tools,
        metadata=metadata,
        references=skill_bundle.files_from_wire([f.model_dump() for f in body.references]),
        assets=skill_bundle.files_from_wire([f.model_dump() for f in body.assets]),
        scripts=skill_bundle.files_from_wire([f.model_dump() for f in body.scripts]),
    )


def _preview_payload(payload: SkillPayload) -> dict:
    """zip 解析结果预览：正文 + 资源清单（不回传资源正文，避免响应过大）。"""

    meta = payload.metadata
    risk = meta.get("risk")
    category = meta.get("category")
    return {
        "key": payload.key,
        "description": payload.description,
        "instructions": payload.instructions,
        "license": payload.license,
        "compatibility": payload.compatibility,
        "preapproved_tools": payload.preapproved_tools,
        "risk": risk if isinstance(risk, str) and risk in RISK_LEVELS else None,
        "category": category if isinstance(category, str) and category != "" else None,
        "tools": payload.declared_tools(),
        "inject_state": payload.inject_state(),
        "references": skills_service._resource_wire(payload.references, False),
        "assets": skills_service._resource_wire(payload.assets, False),
        "scripts": skills_service._resource_wire(payload.scripts, False),
    }


@router.get("/skills")
async def list_skills(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await skills_service.list_skill_summaries(db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/skills/{key}")
async def get_skill(
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return await skills_service.get_skill_summary(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills")
async def create_skill(
    body: SkillPayloadIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    payload = _payload_from_in(body)
    try:
        row = await skills_service.create_user_skill(
            db, payload, risk=body.risk, category=body.category
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skill.create",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "risk": row.risk,
            "category": row.category,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.patch("/skills/{key}")
async def update_skill(
    key: str,
    body: SkillPayloadIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    payload = _payload_from_in(body)
    try:
        row = await skills_service.update_user_skill(
            db, key, payload, risk=body.risk, category=body.category
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skill.update",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "risk": row.risk,
            "category": row.category,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.delete("/skills/{key}")
async def delete_skill(
    key: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        await skills_service.delete_user_skill(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skill.delete",
        resource_type="skill",
        resource_id=key,
    )
    return {"deleted": key}


@router.post("/skills/parse")
async def parse_skill_zip(
    file: UploadFile = File(...),
    _user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """解析 zip 技能包并回预览；不写库。"""

    if file.filename is None or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 技能包")
    raw = await file.read()
    try:
        payload = skill_bundle.payload_from_zip(raw)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "解析技能包 filename=%s key=%s references=%s assets=%s scripts=%s",
        file.filename,
        payload.key,
        len(payload.references),
        len(payload.assets),
        len(payload.scripts),
    )
    return _preview_payload(payload)


@router.post("/skills/import")
async def import_skill_zip(
    file: UploadFile = File(...),
    risk: str | None = Form(None),
    category: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """导入 zip 技能包；risk/category 以包内 metadata 为准，缺失时必须显式给出。"""

    if file.filename is None or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 技能包")
    raw = await file.read()
    try:
        payload = skill_bundle.payload_from_zip(raw)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = payload.metadata
    resolved_risk = meta.get("risk") if isinstance(meta.get("risk"), str) else risk
    resolved_category = (
        meta.get("category") if isinstance(meta.get("category"), str) else category
    )
    if resolved_risk is None or resolved_risk == "":
        raise HTTPException(status_code=400, detail="技能包未声明风险等级，请在导入时选择")
    if resolved_category is None or resolved_category == "":
        raise HTTPException(status_code=400, detail="技能包未声明分类，请在导入时填写")

    try:
        row = await skills_service.create_user_skill(
            db,
            payload,
            risk=str(resolved_risk),
            category=str(resolved_category),
            # 第三方技能包允许声明平台暂未实现的工具，与磁盘技能同口径
            forbid_unknown_tools=False,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "导入技能 filename=%s key=%s references=%s assets=%s scripts=%s",
        file.filename,
        row.key,
        len(payload.references),
        len(payload.assets),
        len(payload.scripts),
    )
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skill.import",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "filename": file.filename,
            "risk": row.risk,
            "category": row.category,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.get("/skills/{key}/export")
async def export_skill(
    key: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> Response:
    """导出为 zip（SKILL.md + references/ + assets/ + scripts/），可直接再次导入。"""

    try:
        raw = await skills_service.export_skill_zip(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skill.export",
        resource_type="skill",
        resource_id=key,
        detail={"bytes": len(raw)},
    )
    return Response(
        content=raw,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{key}.zip"'},
    )


@router.post("/skills/reload")
async def reload_skills(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """从磁盘重装内置技能；frontmatter 非法时显式失败。

    重载会改变可装配的工具面，属于配置变更，必须留痕。
    """

    try:
        disk_items = await skills_service.disk_skill_summaries(db)
        items = await skills_service.list_skill_summaries(db)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="skills.reload",
        resource_type="skill",
        detail={
            "total": len(items),
            "disk": len(disk_items),
            "categories": sorted({str(i.get("category")) for i in items}),
        },
    )
    return {"items": items, "total": len(items)}
