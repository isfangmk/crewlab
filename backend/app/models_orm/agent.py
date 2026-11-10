"""用户自定义智能体配置表。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class UserAgent(Base):
    """用户自定义助理；内置 default 不落库。"""

    __tablename__ = "user_agents"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_use: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    critical_reminder: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools_json: Mapped[str] = mapped_column(Text, nullable=False)
    disallowed_tools_json: Mapped[str] = mapped_column(Text, nullable=False)
    permission_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    # 输出格式约束：free / json / checklist，运行时由服务端生成固定文案
    output_format: Mapped[str] = mapped_column(
        String(32), nullable=False, default="free", server_default="free"
    )
    # 少样本示例：[{input, output}]，最多 3 条
    examples_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    # 知识范围：{"paths": [...], "doc_ids": [...]}，空 = 继承全部知识库
    knowledge_scope_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default='{"paths": [], "doc_ids": []}',
        server_default='{"paths": [], "doc_ids": []}',
    )
    # 勾选的技能 key；NULL = 继承全部技能，'[]' = 不装配任何技能
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
