"""评测运行只读分享令牌。

与会话分享（share.ShareToken 硬绑 chat_sessions）分开建表，
避免动既有表结构；token 存哈希，明文只在创建时返回一次。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import AwareDateTime, Base


class EvalShareToken(Base):
    __tablename__ = "eval_share_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(
        ForeignKey("eval_runs.run_uid", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
