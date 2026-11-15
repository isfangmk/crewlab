"""外部知识源：URL 页面或 Git 仓库的同步配置与状态。

同步出的文档仍落在 knowledge_docs，source_type 为 url / git；
本表只保存接入配置和最近一次同步结果，便于页面展示与重试。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AwareDateTime, Base

if TYPE_CHECKING:
    from app.models.knowledge import KnowledgeDoc


class KnowledgeSource(Base):
    """外部源配置；kind=url 拉取单页，kind=git 拉取仓库内文档。"""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # url | git
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Git 分支；URL 源为空
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # Git 仓库内的相对目录过滤；为空表示全仓库
    path_prefix: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 同步文档在知识库中的目录前缀；为空时默认「外部源/<名称>」
    knowledge_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # idle | syncing | ok | error
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # URL 条件请求：服务端支持时 304 直接跳过正文下载
    etag: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    last_modified: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    docs: Mapped[list[KnowledgeDoc]] = relationship(
        back_populates="external_source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
