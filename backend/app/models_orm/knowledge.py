"""知识库文档与向量切块表。"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models_orm.base import Base

# 与 EMBEDDING_DIM=2560 对齐；启动时由嵌入客户端再校验实际向量长度
EMBEDDING_VECTOR_DIM = 2560


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
    tags: Mapped[list] = mapped_column(JSONB, nullable=False)
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
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(Base):
    """文档切块及 pgvector 嵌入。"""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_VECTOR_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")
