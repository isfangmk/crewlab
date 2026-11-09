"""API 依赖：鉴权与角色。"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.request_context import set_user_id
from app.services.auth_service import AuthUser, resolve_bearer


async def require_auth(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthUser:
    """校验 Bearer Token（api_tokens 表）；失败显式 401。"""

    if authorization is None:
        raise HTTPException(status_code=401, detail="缺少 Authorization")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Authorization 必须是 Bearer")
    raw = authorization[len(prefix) :]
    try:
        user = await resolve_bearer(db, raw)
    except LookupError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    set_user_id(str(user.id))
    return user


async def require_admin(user: AuthUser = Depends(require_auth)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要 admin 角色")
    return user


async def require_not_viewer(user: AuthUser = Depends(require_auth)) -> AuthUser:
    if user.role == "viewer":
        raise HTTPException(status_code=403, detail="只读用户不可执行此操作")
    return user
