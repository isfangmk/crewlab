"""MCP Server 配置表与工具向量表。"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models_orm.base import Base
from app.models_orm.knowledge import EMBEDDING_VECTOR_DIM


class McpServer(Base):
    """外部 MCP Server 注册信息。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    credential: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    header_template_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_cache_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tool_embeddings: Mapped[list[McpToolEmbedding]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class McpToolEmbedding(Base):
    """MCP 工具检索向量（与知识库同维度，供语义 search_tools 使用）。"""

    __tablename__ = "mcp_tool_embeddings"
    __table_args__ = (
        UniqueConstraint("server_id", "tool_name", name="uq_mcp_tool_embeddings_server_tool"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    embedding = mapped_column(Vector(EMBEDDING_VECTOR_DIM), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    server: Mapped[McpServer] = relationship(back_populates="tool_embeddings")
