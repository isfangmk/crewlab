"""审批规则表：工具×助理×风险 的自动放行/需审批/禁用/升级策略。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class ApprovalRule(Base):
    """一条审批规则；多条命中时按确定性优先级取首条（见 approval_rule_service）。"""

    __tablename__ = "approval_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 停用的规则不参与匹配，保留配置便于恢复
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 工具名通配（fnmatch 语法）：run_workflow / mcp__obs-tools__* / *
    tool_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    # 空 = 全部助理；非空 = 仅该助理（agent name）
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 空 = 不限风险；safe | mutating | dangerous（三级危险度）
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # auto_approve 免审直跑 | require_approval 强制进闸 | block 禁用 | escalate 进闸且仅 admin 可批
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # 大者优先；同具体度下先比 priority
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
