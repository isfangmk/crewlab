"""用户自定义技能表（SKILL.md 全套资源存库）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class UserSkill(Base):
    """用户创建的技能；内置磁盘技能（backend/skills）不落库。

    字段与 ADK 的 Skill 模型一一对应：
      - frontmatter 的 name/description/allowed-tools 拆列存，便于列表筛选；
        metadata 与其它扩展字段落 metadata_json。
      - SKILL.md 正文（L2 指令）落 instructions。
      - references/ assets/ scripts/（L3 资源）按「相对路径 → 内容」存 JSON；
        assets 允许二进制，统一 base64。
    """

    __tablename__ = "user_skills"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # frontmatter 的 allowed-tools（ADK 实验字段，空格分隔；本平台不据此放行审批）
    preapproved_tools: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="", server_default=""
    )
    # 平台侧风险等级（safe / mutating / dangerous），ADK 规范外、本平台新增
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    # 平台侧分组（内置工具同一套分组语义），驱动技能目录页的筛选条与分组表。
    # 列名用 skill_group：group 是 PostgreSQL 保留字。
    skill_group: Mapped[str] = mapped_column(
        String(32), nullable=False, default="other", server_default="other"
    )
    # 来源类型：created = 平台内新建；imported = zip 导入。
    # 内置磁盘技能不落库，来源由目录推断为 builtin。
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="created", server_default="created"
    )
    # SKILL.md 正文（frontmatter 之后的 Markdown）
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    # frontmatter.metadata：adk_additional_tools / adk_inject_state 等
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    # 资源目录：JSON 数组 [{path, text} | {path, base64}]
    references_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    assets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    scripts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
