"""评测运行只读分享令牌：创建与解析。

与会话分享同口径：明文 token 只在创建时返回一次，库里存哈希；
过期时间必填，读取时逐次校验，不做撤销。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.eval_share import EvalShareToken
from app.services.auth_service import hash_token

logger = logging.getLogger(__name__)

MAX_EXPIRES_IN_HOURS = 720


async def create_eval_share(
    db: AsyncSession,
    *,
    run_uid: str,
    created_by: int | None,
    expires_in_hours: int,
) -> tuple[str, EvalShareToken]:
    if run_uid == "":
        raise ValueError("run_uid 不能为空")
    if expires_in_hours < 1 or expires_in_hours > MAX_EXPIRES_IN_HOURS:
        raise ValueError(f"expires_in_hours 必须在 1–{MAX_EXPIRES_IN_HOURS} 之间")
    raw = secrets.token_urlsafe(24)
    row = EvalShareToken(
        run_uid=run_uid,
        token_hash=hash_token(raw),
        created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建评测分享 run_uid=%s expires_at=%s by=%s",
        run_uid,
        row.expires_at.isoformat(),
        created_by,
    )
    return raw, row


async def resolve_eval_share(db: AsyncSession, raw_token: str) -> EvalShareToken:
    """按明文 token 取分享行；缺失或过期都显式失败。"""

    if raw_token == "":
        raise LookupError("分享令牌无效")
    row = await db.scalar(
        select(EvalShareToken).where(EvalShareToken.token_hash == hash_token(raw_token))
    )
    if row is None:
        raise LookupError("分享令牌无效")
    if row.expires_at < datetime.now(timezone.utc):
        raise LookupError("分享令牌已过期")
    return row
