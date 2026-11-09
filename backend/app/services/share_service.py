"""会话只读分享令牌。"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.share import ShareToken
from app.services.auth_service import hash_token

logger = logging.getLogger(__name__)


async def create_share(
    db: AsyncSession,
    *,
    session_id: str,
    created_by: int,
    expires_in_hours: int,
) -> tuple[str, ShareToken]:
    if session_id == "":
        raise ValueError("session_id 不能为空")
    if expires_in_hours < 1:
        raise ValueError("expires_in_hours 必须 >= 1")
    raw = secrets.token_urlsafe(24)
    row = ShareToken(
        session_id=session_id,
        token_hash=hash_token(raw),
        created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建分享 session_id=%s expires_at=%s by=%s",
        session_id,
        row.expires_at.isoformat(),
        created_by,
    )
    return raw, row


async def resolve_share(db: AsyncSession, raw_token: str) -> str:
    if raw_token == "":
        raise LookupError("分享令牌无效")
    row = await db.scalar(
        select(ShareToken).where(ShareToken.token_hash == hash_token(raw_token))
    )
    if row is None:
        raise LookupError("分享令牌无效")
    if row.expires_at < datetime.now(timezone.utc):
        raise LookupError("分享令牌已过期")
    return row.session_id
