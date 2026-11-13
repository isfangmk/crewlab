"""MCP Server 配置表与工具向量元数据表。

工具向量本体存 app.vector_store（ref_id = 工具 wire 名）；
本表只留检索元数据（名称 / 检索文本 / 内容哈希），用于向量复用与一致性校验。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models_orm.base import AwareDateTime, Base


class McpServer(Base):
    """外部 MCP Server 注册信息。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    credential: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    header_template_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # never | non_readonly | always；trusted=true 时运行时视为 never
    approval_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="always")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_cache_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 工具向量同步状态：idle | syncing | ok | error
    embedding_sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    embedding_sync_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_sync_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding_synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
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
    """MCP 工具检索向量元数据（向量本体在 app.vector_store）。"""

    __tablename__ = "mcp_tool_embeddings"
    __table_args__ = (
        UniqueConstraint("server_id", "tool_name", name="uq_mcp_tool_embeddings_server_tool"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    server: Mapped[McpServer] = relationship(back_populates="tool_embeddings")
