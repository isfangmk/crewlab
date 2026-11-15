"""知识库文档与切块表。

向量不在这里：KnowledgeChunk 只存切块正文等业务数据，
向量统一进 app.vector_store（与业务库分离、可切换实现）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AwareDateTime, Base

if TYPE_CHECKING:
    from app.models.knowledge_source import KnowledgeSource

# PG 用 JSONB 支持 containment 过滤；SQLite 用 JSON 文本（过滤走 json_each）
TagsJSON = JSON().with_variant(JSONB(), "postgresql")


class KnowledgeDoc(Base):
    """知识库文档正文与元数据。"""

    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    title_en: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tags: Mapped[list] = mapped_column(TagsJSON, nullable=False)
    # 停用后不参与检索，切块保留，可随时恢复
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 上传原件信息：抽取后的正文与原件体积可能差很多（尤其 PDF）
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 检索命中累计：运营侧判断哪些文档真被用到；一次检索对同一文档只计 1 次
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    # 外部源同步归属：手工 / 上传文档为空；external_key 在来源内唯一标识原文，
    # external_hash 用于增量同步时跳过未变化的文档
    external_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    external_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    external_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    external_source: Mapped[KnowledgeSource | None] = relationship(
        back_populates="docs"
    )


class KnowledgeChunk(Base):
    """文档切块；向量按切块 id 存在向量库里。"""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")
