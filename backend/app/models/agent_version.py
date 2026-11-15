"""助理配置版本快照表。

每次创建/更新助理都落一份完整配置快照，回滚时读快照覆盖当前配置；
版本号在单个助理内单调递增，回滚会再产生一版并记录来源版本。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class UserAgentVersion(Base):
    """一次助理配置快照。"""

    __tablename__ = "user_agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_name", "version", name="uq_user_agent_versions_agent_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 助理删除时快照一并删除，避免残留不可恢复的孤儿历史
    agent_name: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("user_agents.name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 单个助理内单调递增；版本 1 = 创建时的初始配置
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 整份 UserAgentInput 序列化，回滚时据此覆盖当前配置
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", server_default=""
    )
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    # 回滚产生时记录来源版本；正常保存为 NULL
    restored_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
