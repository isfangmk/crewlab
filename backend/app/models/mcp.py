"""MCP Server 配置表与工具向量元数据表。

工具向量本体存 app.vector_store（ref_id = 工具 wire 名）；
本表只留检索元数据（名称 / 检索文本 / 内容哈希），用于向量复用与一致性校验。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AwareDateTime, Base


class McpServer(Base):
    """外部 MCP Server 注册信息。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # http | stdio
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    # http 的端点 URL；stdio 时留空
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # stdio 专用：以服务端进程权限运行本机命令，仅管理员可配
    command: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cwd: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # env 值支持 {{字段}} 占位符，字段从 credential 指定的凭证注入
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    credential: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    header_template_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_cache_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 资源/提示词缓存：探测时写入，列表展示不连远程
    resources_cache_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    prompts_cache_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    resource_templates_cache_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    last_probed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
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
