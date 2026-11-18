"""登录与用户管理。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_auth
from app.db import get_db
from app.models.user import User
from app.services.observability.audit_service import write_audit
from app.services.platform import auth_service
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    """登录请求体。"""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, description="登录邮箱")
    password: str = Field(min_length=1, description="登录密码")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth 响应字段名，不是密钥
    role: str
    email: str


class CreateUserRequest(BaseModel):
    """新建用户的请求体。"""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, description="用户登录邮箱")
    display_name: str = Field(min_length=1, description="用户显示名称")
    password: str = Field(min_length=1, description="初始登录密码")
    role: str = Field(min_length=1, description="用户角色：admin 管理员 / developer 开发者 / viewer 只读")


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    status: str
    created_at: str | None = None


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int


class UserOptionOut(BaseModel):
    """用户下拉选项：只有 id 与邮箱。"""

    id: int
    email: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    """邮箱密码登录，校验通过后签发 Bearer access_token。"""

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


@router.get("/users", response_model=UserListOut)
async def list_users(
    q: str | None = Query(None, max_length=200, description="按邮箱或显示名称模糊筛选，最长 200 字；留空不过滤"),
    role: str | None = Query(None, description="按角色筛选：admin / developer / viewer；留空不过滤"),
    status: str | None = Query(None, description="按状态筛选：active 启用 / disabled 停用；留空不过滤"),
    sort_dir: str = Query("desc", description="排序方向：desc 最新在前，asc 最早在前"),
    limit: int = Query(20, ge=1, le=200, description="每页条数，1~200"),
    offset: int = Query(0, ge=0, description="分页偏移量，从 0 开始"),
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> UserListOut:
    """用户列表：服务端筛选 + 分页，返回 items 与命中总数。"""

    if role is not None and role not in auth_service.known_roles():
        raise HTTPException(status_code=400, detail=f"role 非法: {role}")
    if status is not None and status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail=f"status 非法: {status}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"sort_dir 非法: {sort_dir}")

    rows, total = await auth_service.list_users(
        db,
        q=q if q not in (None, "") else None,
        role=role,
        status=status,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return UserListOut(
        items=[
            UserOut(
                id=u.id,
                email=u.email,
                display_name=u.display_name,
                role=u.role,
                status=u.status,
                created_at=u.created_at.isoformat() if u.created_at else None,
            )
            for u in rows
        ],
        total=total,
    )


@router.get("/users/options", response_model=list[UserOptionOut])
async def list_user_options(
    db: AsyncSession = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> list[UserOptionOut]:
    """用户下拉选项（审计页的操作者筛选）；只给 id 与邮箱，不含角色与状态。"""

    rows = (await db.execute(select(User.id, User.email).order_by(User.email))).all()
    return [UserOptionOut(id=row.id, email=row.email) for row in rows]


@router.post("/users", response_model=UserOut)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    """管理员新建用户，返回创建后的用户信息。"""
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
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


class SetStatusRequest(BaseModel):
    """设置用户状态请求体。"""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, description="目标状态：active 启用 / disabled 停用")


@router.post("/users/{user_id}/status", response_model=UserOut)
async def set_status(
    user_id: int = Path(description="要设置状态的用户 id"),
    body: SetStatusRequest = Body(description="目标状态"),
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    """管理员启用或停用指定用户（停用后该用户无法登录）。"""
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
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


class UpdateRoleRequest(BaseModel):
    """修改用户角色请求体。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, description="目标角色：admin 管理员 / developer 开发者 / viewer 只读")


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def update_role(
    user_id: int = Path(description="要修改角色的用户 id"),
    body: UpdateRoleRequest = Body(description="目标角色"),
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> UserOut:
    """管理员修改指定用户的角色；不允许修改当前登录账号自己的角色。"""
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
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


class ResetPasswordRequest(BaseModel):
    """重置密码请求体。"""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, description="要设置的新登录密码")


@router.post("/users/{user_id}/password", status_code=204)
async def reset_password(
    user_id: int = Path(description="要重置密码的用户 id"),
    body: ResetPasswordRequest = Body(description="新密码"),
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> None:
    """管理员重置指定用户的登录密码；成功返回 204 无响应体。"""
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
    user_id: int = Path(description="要删除的用户 id"),
    db: AsyncSession = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> None:
    """管理员删除指定用户；不允许删除当前登录账号，成功返回 204。"""
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
