"""最小登录：固定管理员邮箱换取 Bearer Token。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_auth
from app.config import get_settings

router = APIRouter(tags=["auth"])

ADMIN_EMAIL = "admin@local"


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = "admin"
    email: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    settings = get_settings()
    if body.email != ADMIN_EMAIL or body.password != settings.auth_token:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    return LoginResponse(
        access_token=settings.auth_token,
        email=ADMIN_EMAIL,
        role="admin",
    )


@router.get("/auth/self")
async def self(_user: str = Depends(require_auth)) -> dict:
    return {"id": "1", "email": ADMIN_EMAIL, "role": "admin"}


@router.get("/me")
async def me(_user: str = Depends(require_auth)) -> dict:
    """前端 useMe / getMe 使用的当前用户。"""

    return {
        "id": 1,
        "email": ADMIN_EMAIL,
        "display_name": "Admin",
        "phone": "-",
        "role": "admin",
        "status": "active",
        "memberships": [],
    }
