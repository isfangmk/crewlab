"""MCP OAuth 授权：token 存储与在途授权 state。

Token 服务级共享（admin 授权一次全工作台生效），payload 整体 Fernet 加密；
state 一次性用于授权码回调的 CSRF 防护，回调后即删。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import AwareDateTime, Base


class McpOauthToken(Base):
    __tablename__ = "mcp_oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # 加密 JSON：access_token/refresh_token/expires_at/client_info/metadata/auth_source
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class McpOauthState(Base):
    __tablename__ = "mcp_oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
