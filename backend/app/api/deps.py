"""API 依赖：鉴权与角色。"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.request_context import set_user_id
from app.services.platform.auth_service import AuthUser, resolve_bearer, resolve_user_id
from app.services.platform.internal_call import (
    ACTOR_HEADER,
    SECRET_HEADER,
    verify_internal_secret,
)


async def require_auth(
    authorization: str | None = Header(default=None),
    internal_secret: str | None = Header(default=None, alias=SECRET_HEADER),
    internal_actor: str | None = Header(default=None, alias=ACTOR_HEADER),
    db: AsyncSession = Depends(get_db),
) -> AuthUser:
    """校验身份：外部走 Bearer Token；平台助手进程内调用走内部凭证。

    内部凭证分支仅在同一进程内可行——密钥进程启动随机生成、不落配置，外部拿不到；
    且 actor 由服务端按会话归属确定，外部无法指定。两条路径解析出的 AuthUser 完全等价，
    后续 require_admin / require_not_viewer 原样生效，权限与页面一致。
    """

    # 内部凭证分支：密钥对才认 actor，否则一律按外部 Bearer 处理
    if internal_secret is not None or internal_actor is not None:
        if not verify_internal_secret(internal_secret):
            raise HTTPException(status_code=401, detail="内部调用凭证无效")
        if not isinstance(internal_actor, str) or not internal_actor.isdigit():
            raise HTTPException(status_code=401, detail="内部调用身份无效")
        try:
            user = await resolve_user_id(db, int(internal_actor))
        except LookupError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        set_user_id(str(user.id))
        return user

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
