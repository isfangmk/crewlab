"""凭证库 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import secrets_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

router = APIRouter(tags=["secrets"])


class SecretCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    # 描述是必填：凭证没有说明就没法在列表里辨认用途。
    # 留默认值等于允许静默填空。
    description: str
    fields: dict[str, str]


@router.get("/credential-types")
async def list_credential_types(_user: str = Depends(require_auth)) -> dict:
    items = secrets_service.list_credential_types()
    return {"items": items}


@router.get("/secrets")
async def list_secrets(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    rows = await secrets_service.list_secrets(db)
    return {"items": [secrets_service.secret_to_wire(r) for r in rows]}


@router.post("/secrets")
async def create_secret(
    body: SecretCreateIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
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
        db,
        actor_user_id=user.id,
        action="secret.create",
        resource_type="secret",
        resource_id=str(row.id),
        detail={"name": row.name, "type": row.cred_type},
    )
    return secrets_service.secret_to_wire(row)


@router.delete("/secrets/{secret_id}")
async def delete_secret(
    secret_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        name, cred_type = await secrets_service.delete_secret(db, secret_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="secret.delete",
        resource_type="secret",
        resource_id=str(secret_id),
        detail={"name": name, "type": cred_type},
    )
    return {"ok": True}
