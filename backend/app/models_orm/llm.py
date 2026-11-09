"""模型配置表：对话模型列表的唯一来源。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class LlmProvider(Base):
    """一个 LLM Provider 及其可用模型列表。"""

    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    # 逗号分隔的模型名，顺序敏感；index 0 可作为默认候选
    models: Mapped[str] = mapped_column(Text, nullable=False)
    default_model: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # litellm 模型名前缀（如 openai/）；空串表示不加前缀。
    # model_prefix 按厂商配置；不能对所有 provider 写死 openai/。
    model_prefix: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 厂商专属请求体（JSON 文本），如 DeepSeek 的 {"thinking":{"type":"enabled"}}。
    # 只对确实需要它的 provider 配，不能强加给所有 provider。
    extra_body_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AppSetting(Base):
    """键值配置（如默认 provider）。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
