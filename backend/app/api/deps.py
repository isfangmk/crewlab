"""API 依赖：鉴权。"""

from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_auth(authorization: str | None = Header(default=None)) -> str:
    """校验 Bearer Token；缺失或不匹配直接 401。"""

    if authorization is None:
        raise HTTPException(status_code=401, detail="缺少 Authorization")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Authorization 必须是 Bearer")
    token = authorization[len(prefix) :]
    expected = get_settings().auth_token
    if token != expected:
        raise HTTPException(status_code=401, detail="无效 Token")
    return "admin"
