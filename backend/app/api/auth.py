"""登录与用户管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.services import auth_service
from app.services.auth_service import AuthUser
from app.services.audit_service import write_audit

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: str = Field(min_length=1)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    status: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    try:
        user, token = await auth_service.authenticate(db, body.email, body.password)
    except LookupError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 只记成功登录：失败尝试来自未认证请求，逐次落库会把审计表变成放大器。
    await write_audit(
        actor_user_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        detail={"email": user.email, "role": user.role},
    )
    return LoginResponse(access_token=token, email=user.email, role=user.role)


@router.get("/me")
async def me(user: AuthUser = Depends(require_auth)) -> dict:
    """前端 useMe / getMe 使用的当前用户。"""

    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "phone": "-",
        "role": user.role,
        "status": "active",
        "memberships": [],
    }


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> list[UserOut]:
    rows = await auth_service.list_users(db)
    return [
        UserOut(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=u.role,
            status=u.status,
        )
        for u in rows
    ]


@router.post("/users", response_model=UserOut)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    try:
        row = await auth_service.create_user(
            db,
            email=body.email,
            display_name=body.display_name,
            password=body.password,
            role=body.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=admin.id,
        action="user.create",
        resource_type="user",
        resource_id=str(row.id),
        detail={"email": row.email, "role": row.role},
    )
    return UserOut(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        status=row.status,
    )


class SetStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1)


@router.post("/users/{user_id}/status", response_model=UserOut)
async def set_status(
    user_id: int,
    body: SetStatusRequest,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    try:
        row = await auth_service.set_user_status(db, user_id, body.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=admin.id,
        action="user.status",
        resource_type="user",
        resource_id=str(row.id),
        detail={"status": row.status},
    )
    return UserOut(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        status=row.status,
    )


class UpdateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def update_role(
    user_id: int,
    body: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能修改当前登录账号的角色")
    try:
        row = await auth_service.update_user_role(db, user_id, body.role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=admin.id,
        action="user.role",
        resource_type="user",
        resource_id=str(row.id),
        detail={"email": row.email, "role": row.role},
    )
    return UserOut(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        status=row.status,
    )


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1)


@router.post("/users/{user_id}/password", status_code=204)
async def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> None:
    try:
        row = await auth_service.reset_user_password(db, user_id, body.password)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=admin.id,
        action="user.password_reset",
        resource_type="user",
        resource_id=str(row.id),
        detail={"email": row.email},
    )


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    try:
        email = await auth_service.delete_user(db, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=admin.id,
        action="user.delete",
        resource_type="user",
        resource_id=str(user_id),
        detail={"email": email},
    )
