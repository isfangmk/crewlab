"""会话与消息持久化。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class ChatSession(Base):
    """工作台对话会话元数据。"""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 全局助理引用的页面快照（JSON）；没有引用时为 NULL
    page_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    adk_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatMessage(Base):
    """投影到前端的消息（含工具意图 / 工具卡 / 正文）。"""

    __tablename__ = "chat_messages"

    # 含流式合成 id，例如 tool-card-<tool_call_id>
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # user | assistant | tool_intent | tool_card | system
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="message")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户消息引用的附件 id 列表（JSON 数组）；无附件时为 NULL
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
