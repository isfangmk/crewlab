"""对外发布渠道：一个渠道 = 一把 API Key + 一个助理 + 一个持久会话。

Key 只存 sha256；原文仅在创建/轮换时返回一次（与分享令牌同口径）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class PublishedChannel(Base):
    """已发布的 OpenAI 兼容渠道。"""

    __tablename__ = "published_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 渠道名同时用于观测记账（observation_turns.channel），因此要求唯一
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # sha256(原文)；原文只在签发响应里出现一次
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # Key 前缀（用于列表掩码展示）
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # 渠道复用的对话会话；为空 = 尚未发生第一次调用
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # NULL = 永不过期
    expires_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
