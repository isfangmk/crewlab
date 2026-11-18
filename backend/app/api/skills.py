"""技能目录 API：内置技能只读，用户技能可增删改 + zip 导入导出。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Path, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.schemas.skill import SkillPayloadIn
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser
from app.services.skills import skill_bundle, skill_groups, skill_install_service, skills_service
from app.services.skills.skill_bundle import RISK_LEVELS, SkillPayload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


class InstallFromUrlIn(BaseModel):
    """从网址安装技能；risk/group 在技能包未声明时必填。"""

    url: str = Field(min_length=1, description="技能包 zip 直链或 git 仓库地址，不能为空")
    risk: str | None = Field(default=None, description="风险等级：safe / mutating / dangerous；技能包未声明时必填，留空则取包内值")
    group: str | None = Field(default=None, description="技能分组 key（如 observe、device、cloud 等）；技能包未声明时必填，留空则取包内值")


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
        preapproved_tools=body.preapproved_tools,
        metadata=metadata,
        references=skill_bundle.files_from_wire([f.model_dump() for f in body.references]),
        assets=skill_bundle.files_from_wire([f.model_dump() for f in body.assets]),
        scripts=skill_bundle.scripts_from_wire([f.model_dump() for f in body.scripts]),
    )


def _preview_payload(payload: SkillPayload) -> dict:
    """zip 解析结果预览：正文 + 资源清单（不回传资源正文，避免响应过大）。"""

    meta = payload.metadata
    risk = meta.get("risk")
    group = meta.get("group")
    return {
        "key": payload.key,
        "description": payload.description,
        "instructions": payload.instructions,
        "preapproved_tools": payload.preapproved_tools,
        "risk": risk if isinstance(risk, str) and risk in RISK_LEVELS else None,
        "group": group if isinstance(group, str) and group != "" else None,
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
    """列出全部技能摘要（内置 + 用户自定义），含分组、风险与资源统计。"""

    try:
        items = await skills_service.list_skill_summaries(db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/skills/groups")
async def list_skill_groups(
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """技能分组目录：固定枚举，前端按 key 渲染筛选条与分组标题。"""

    return {"groups": skill_groups.skill_group_wire()}


@router.get("/skills/{key}")
async def get_skill(
    key: str = Path(description="要查看的技能 key（先通过列表获取）"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """按 key 获取单个技能详情，含完整指令与资源清单。"""

    try:
        return await skills_service.get_skill_summary(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills")
async def create_skill(
    body: SkillPayloadIn = Body(description="新技能定义：key、描述、指令、工具与资源等"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """新建用户自定义技能，并持久化到库。"""

    payload = _payload_from_in(body)
    try:
        row = await skills_service.create_user_skill(
            db, payload, risk=body.risk, group=body.group
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="skill.create",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "risk": row.risk,
            "group": row.skill_group,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.patch("/skills/{key}")
async def update_skill(
    key: str = Path(description="要更新的技能 key"),
    body: SkillPayloadIn = Body(description="技能的完整新定义"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按 key 更新用户自定义技能，内置技能只读不可改。"""

    payload = _payload_from_in(body)
    try:
        row = await skills_service.update_user_skill(
            db, key, payload, risk=body.risk, group=body.group
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="skill.update",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "risk": row.risk,
            "group": row.skill_group,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.delete("/skills/{key}")
async def delete_skill(
    key: str = Path(description="要删除的技能 key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按 key 删除用户自定义技能，内置技能不可删。"""

    try:
        await skills_service.delete_user_skill(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="skill.delete",
        resource_type="skill",
        resource_id=key,
    )
    return {"deleted": key}


@router.post("/skills/install-from-url")
async def install_skill_from_url(
    body: InstallFromUrlIn = Body(description="技能包地址及可选的风险等级、分组"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """从网址安装技能（zip 直链或 git 仓库），供非对话/前端直接复用。

    SSRF 防护、risk/group 校验、审计均在 service 层统一完成；这里只做入参转换。
    """

    try:
        result = await skill_install_service.install_skill_from_url(
            db,
            url=body.url,
            risk=body.risk,
            group=body.group,
            owner_user_id=user.id,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("REST 安装技能 url=%s key=%s", body.url, result["key"])
    return result


@router.post("/skills/parse")
async def parse_skill_zip(
    file: UploadFile = File(..., description="要解析的 .zip 技能包"),
    _user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """解析 zip 技能包并回预览；不写库。不进审计：导入（skill.import）才记。"""

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
    file: UploadFile = File(..., description="要导入的 .zip 技能包"),
    risk: str | None = Form(default=None, description="风险等级：safe / mutating / dangerous；包内 metadata 未声明时必填"),
    group: str | None = Form(default=None, description="技能分组 key（如 observe、device、cloud 等）；包内 metadata 未声明时必填"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """导入 zip 技能包；risk/group 以包内 metadata 为准，缺失时必须显式给出。"""

    if file.filename is None or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 技能包")
    raw = await file.read()
    try:
        payload = skill_bundle.payload_from_zip(raw)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta = payload.metadata
    resolved_risk = meta.get("risk") if isinstance(meta.get("risk"), str) else risk
    resolved_group = meta.get("group") if isinstance(meta.get("group"), str) else group
    if resolved_risk is None or resolved_risk == "":
        raise HTTPException(status_code=400, detail="技能包未声明风险等级，请在导入时选择")
    if resolved_group is None or resolved_group == "":
        raise HTTPException(status_code=400, detail="技能包未声明分组，请在导入时选择")

    try:
        row = await skills_service.create_user_skill(
            db,
            payload,
            risk=str(resolved_risk),
            group=str(resolved_group),
            # 第三方技能包允许声明平台暂未实现的工具，与磁盘技能同口径
            forbid_unknown_tools=False,
            origin="imported",
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
        actor_user_id=user.id,
        action="skill.import",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "filename": file.filename,
            "risk": row.risk,
            "group": row.skill_group,
            "tools": payload.declared_tools(),
            "references": len(payload.references),
            "assets": len(payload.assets),
            "scripts": len(payload.scripts),
        },
    )
    return await skills_service.get_skill_summary(db, row.key)


@router.get("/skills/{key}/export")
async def export_skill(
    key: str = Path(description="要导出的技能 key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> Response:
    """导出为 zip（SKILL.md + references/ + assets/ + scripts/），可直接再次导入。"""

    try:
        raw = await skills_service.export_skill_zip(db, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
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
        actor_user_id=user.id,
        action="skills.reload",
        resource_type="skill",
        detail={
            "total": len(items),
            "disk": len(disk_items),
            "groups": sorted({str(i.get("group")) for i in items}),
        },
    )
    return {"items": items, "total": len(items)}
