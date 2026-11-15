"""跨会话长期记忆：按助理（或全局）作用域存储的语义条目。

向量写入复用 app.vector_store 的 MEMORY_NAMESPACE；
`VECTOR_BACKEND=none` 时记忆功能显式不可用（依赖语义检索）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class MemoryItem(Base):
    """一条长期记忆；scope_agent_id 为空 = 全局记忆（所有助理可回忆）。"""

    __tablename__ = "memory_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = 全局；否则只有该助理（及其会话）可回忆
    scope_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
