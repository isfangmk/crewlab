"""本地用户鉴权：密码哈希、Token 签发、bootstrap 管理员。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_orm.user import ApiToken, User

logger = logging.getLogger(__name__)

_ROLES = frozenset({"admin", "developer", "viewer"})
_PBKDF2_ITERS = 120_000


def bootstrap_admin_email() -> str:
    """读取 bootstrap 管理员邮箱；空串显式失败。"""

    email = get_settings().auth_admin_email
    if email == "":
        raise ValueError("AUTH_ADMIN_EMAIL 不能为空")
    return email


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    display_name: str
    role: str


def hash_password(password: str) -> str:
    if password == "":
        raise ValueError("密码不能为空")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        raise ValueError("password_hash 格式非法")
    iters = int(parts[1])
    salt = parts[2]
    expected = parts[3]
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iters
    ).hex()
    return hmac.compare_digest(digest, expected)


def hash_token(raw_token: str) -> str:
    if raw_token == "":
        raise ValueError("token 不能为空")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def ensure_bootstrap_admin(db: AsyncSession) -> User:
    """库中无用户时，用 AUTH_ADMIN_EMAIL + AUTH_TOKEN 创建首个管理员。"""

    admin_email = bootstrap_admin_email()
    existing = await db.scalar(select(User).where(User.email == admin_email))
    if existing is not None:
        return existing
    count = await db.scalar(select(User.id).limit(1))
    if count is not None:
        raise RuntimeError(
            f"已有用户但缺少 bootstrap 管理员 {admin_email}，拒绝静默创建"
        )
    settings = get_settings()
    row = User(
        email=admin_email,
        display_name="Admin",
        password_hash=hash_password(settings.auth_token),
        role="admin",
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("已 bootstrap 本地管理员 email=%s", admin_email)
    return row


async def issue_token(db: AsyncSession, user: User, *, days: int = 30) -> str:
    raw = secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
    )
    db.add(row)
    await db.commit()
    logger.info("已签发 API Token user_id=%s", user.id)
    return raw


async def authenticate(db: AsyncSession, email: str, password: str) -> tuple[User, str]:
    if email == "":
        raise ValueError("email 不能为空")
    if password == "":
        raise ValueError("password 不能为空")
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        raise LookupError("邮箱或密码错误")
    if user.status != "active":
        raise PermissionError("用户已禁用")
    if not verify_password(password, user.password_hash):
        raise LookupError("邮箱或密码错误")
    token = await issue_token(db, user)
    return user, token


async def resolve_bearer(db: AsyncSession, raw_token: str) -> AuthUser:
    if raw_token == "":
        raise LookupError("无效 Token")
    th = hash_token(raw_token)
    row = await db.scalar(select(ApiToken).where(ApiToken.token_hash == th))
    if row is None:
        raise LookupError("无效 Token")
    if row.expires_at is not None and row.expires_at < datetime.now(timezone.utc):
        raise LookupError("Token 已过期")
    user = await db.get(User, row.user_id)
    if user is None:
        raise LookupError("Token 对应用户不存在")
    if user.status != "active":
        raise PermissionError("用户已禁用")
    return AuthUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    display_name: str,
    password: str,
    role: str,
) -> User:
    if email == "":
        raise ValueError("email 不能为空")
    if display_name == "":
        raise ValueError("display_name 不能为空")
    if role not in _ROLES:
        raise ValueError(f"role 非法: {role}")
    dup = await db.scalar(select(User).where(User.email == email))
    if dup is not None:
        raise ValueError(f"邮箱已存在: {email}")
    row = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role=role,
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("已创建用户 id=%s email=%s role=%s", row.id, row.email, row.role)
    return row


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.scalars(select(User).order_by(User.id))
    return list(result)


async def set_user_status(db: AsyncSession, user_id: int, status: str) -> User:
    if status not in ("active", "disabled"):
        raise ValueError(f"status 非法: {status}")
    user = await db.get(User, user_id)
    if user is None:
        raise LookupError(f"用户不存在: {user_id}")
    if user.email == bootstrap_admin_email() and status == "disabled":
        raise ValueError("不能禁用 bootstrap 管理员")
    user.status = status
    await db.commit()
    await db.refresh(user)
    logger.info("已更新用户状态 id=%s status=%s", user.id, status)
    return user
