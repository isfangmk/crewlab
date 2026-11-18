"""凭证库 API：CRUD + 轮换 + 健康自检 + 脱敏导出。

凭证字段值只写不读：列表、详情、导出都不返回明文，轮换接口也不回显旧值。
整个凭证域仅管理员可访问——托管密钥不是普通业务操作。
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.services.mcp import mcp_service
from app.services.observability.audit_service import write_audit
from app.services.platform import secrets_service
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["secrets"])


class SecretCreateIn(BaseModel):
    """新建凭证的请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="凭证名称，供 MCP 配置引用，须唯一")
    type: str = Field(min_length=1, description="凭证类型：custom / github / aws / tencentcloud")
    # 描述是必填：凭证没有说明就没法在列表里辨认用途。
    # 留默认值等于允许静默填空。
    description: str = Field(description="用途说明，必填，便于在列表中辨认凭证用途")
    fields: dict[str, str] = Field(description="字段值字典，键为字段名、值为字段明文；必填字段由所选类型决定")


class SecretRotateIn(BaseModel):
    """轮换凭证的请求体。"""

    model_config = ConfigDict(extra="forbid")

    # 只提交要换的字段；未提交的字段沿用旧值（旧密文可解密时）。
    fields: dict[str, str] = Field(description="要更新的字段值字典，键为字段名、值为新明文；未提交的字段沿用旧值")


@router.get("/credential-types")
async def list_credential_types(_user: AuthUser = Depends(require_auth)) -> dict:
    """列出可选的凭证类型及其所需字段定义。"""
    items = secrets_service.list_credential_types()
    return {"items": items}


@router.get("/secrets")
async def list_secrets(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列表只回元数据（名称/类型/字段名）：MCP 配置需要选凭证名。

    字段值、密文与统计信息一律不出现在这里；读写值相关的动作才收口到管理员。
    """

    rows = await secrets_service.list_secrets(db)
    return {"items": [secrets_service.secret_to_wire(r) for r in rows]}


@router.get("/secrets/health")
async def secrets_health(
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> dict:
    """逐条尝试解密，汇总是否存在「换了加密密钥导致不可解」的凭证。"""

    rows = await secrets_service.list_secrets(db)
    items: list[dict] = []
    broken = 0
    for row in rows:
        try:
            secrets_service.check_secret(row)
        except (ValueError, TypeError) as exc:
            broken += 1
            items.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "type": row.cred_type,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "type": row.cred_type,
                "ok": True,
                "error": None,
            }
        )
    return {"items": items, "total": len(items), "broken": broken}


@router.get("/secrets/export")
async def export_secrets(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> Response:
    """导出凭证清单：只有元数据，密文与明文都不出库。"""

    rows = await secrets_service.list_secrets(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["名称", "类型", "说明", "字段", "创建时间", "更新时间"])
    for row in rows:
        keys = _field_keys(row)
        writer.writerow(
            [
                row.name,
                row.cred_type,
                row.description,
                ",".join(keys),
                row.created_at.isoformat(),
                row.updated_at.isoformat(),
            ]
        )
    body = "\ufeff" + buf.getvalue()
    await write_audit(
        actor_user_id=user.id,
        action="secret.export",
        resource_type="secret",
        detail={"count": len(rows), "values_exported": False},
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="credentials.csv"'},
    )


def _field_keys(row) -> list[str]:
    """field_keys_json 损坏必须显式失败，不能导出空字段列。"""

    keys = json.loads(row.field_keys_json)
    if not isinstance(keys, list):
        raise HTTPException(
            status_code=500, detail=f"凭证 field_keys_json 损坏 id={row.id}"
        )
    return [str(k) for k in keys]


@router.post("/secrets")
async def create_secret(
    body: SecretCreateIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员新建凭证，字段值加密存储且不回显明文。"""
    try:
        row = await secrets_service.create_secret(
            db,
            name=body.name,
            cred_type=body.type,
            description=body.description,
            fields=body.fields,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="secret.create",
        resource_type="secret",
        resource_id=str(row.id),
        detail={"name": row.name, "type": row.cred_type},
    )
    return secrets_service.secret_to_wire(row)


@router.get("/secrets/{secret_id}/usage")
async def secret_usage(
    secret_id: int = Path(description="要查看影响面的凭证 id"),
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> dict:
    """轮换前的影响面：哪些 MCP Server 正在引用这条凭证。"""

    try:
        row = await secrets_service.get_secret(db, secret_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    servers = await secrets_service.list_secret_usage(db, row.name)
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "enabled": s.enabled,
                "status": s.status,
            }
            for s in servers
        ],
        "total": len(servers),
    }


@router.post("/secrets/{secret_id}/rotate")
async def rotate_secret(
    secret_id: int = Path(description="要轮换的凭证 id"),
    body: SecretRotateIn = Body(description="要更新的字段值；未提交字段沿用旧值"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员轮换凭证：只更新提交的字段，未提交字段沿用旧值，不回显明文。"""
    try:
        row, rotated_fields = await secrets_service.rotate_secret(
            db, secret_id, new_fields=body.fields
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="secret.rotate",
        resource_type="secret",
        resource_id=str(row.id),
        detail={
            "name": row.name,
            "type": row.cred_type,
            "rotated_fields": rotated_fields,
        },
    )
    return secrets_service.secret_to_wire(row)


@router.post("/secrets/{secret_id}/usage/verify")
async def verify_secret_usage(
    secret_id: int = Path(description="要验证的凭证 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """轮换后验证：用新凭证重新探测引用的 MCP Server。"""

    try:
        row = await secrets_service.get_secret(db, secret_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    servers = await secrets_service.list_secret_usage(db, row.name)

    items: list[dict] = []
    failed = 0
    for server in servers:
        if not server.enabled:
            items.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "ok": False,
                    "skipped": True,
                    "tool_count": 0,
                    "error": "已停用，未探测",
                }
            )
            continue
        try:
            tools = await mcp_service.test_server(db, server.id)
        except Exception as exc:  # noqa: BLE001 — 单台失败不能中断整批验证
            failed += 1
            items.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "ok": False,
                    "skipped": False,
                    "tool_count": 0,
                    "error": str(exc),
                }
            )
            continue
        items.append(
            {
                "id": server.id,
                "name": server.name,
                "ok": True,
                "skipped": False,
                "tool_count": len(tools),
                "error": None,
            }
        )

    await write_audit(
        actor_user_id=user.id,
        action="secret.verify",
        resource_type="secret",
        resource_id=str(row.id),
        detail={"name": row.name, "checked": len(items), "failed": failed},
    )
    return {"items": items, "total": len(items), "failed": failed}


@router.delete("/secrets/{secret_id}")
async def delete_secret(
    secret_id: int = Path(description="要删除的凭证 id"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_admin),
) -> dict:
    """管理员删除一条凭证，不可恢复。"""
    try:
        name, cred_type = await secrets_service.delete_secret(db, secret_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="secret.delete",
        resource_type="secret",
        resource_id=str(secret_id),
        detail={"name": name, "type": cred_type},
    )
    return {"ok": True}
