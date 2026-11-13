"""自动化触发器：Cron 定时与 Webhook 触发，驱动工作流或助理会话。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class TriggerRule(Base):
    """触发器规则：到点/被调用时发起一次工作流运行或助理会话。"""

    __tablename__ = "trigger_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # cron | webhook
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # cron 型：五段 cron 表达式；webhook 型为空
    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # workflow | agent
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    workflow_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True
    )
    # agent 型：目标助理 name（UserAgent.name 或内置名）
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # agent 型触发时的用户消息
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # workflow 型触发时的输入值（JSON 对象，键为开始节点变量名）
    values_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # webhook 型：sha256(token)；原文只在创建时返回一次
    webhook_token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", unique=True
    )
    # 调度器缓存的下次触发时间；cron 型由调度器维护
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TriggerRun(Base):
    """一次触发的执行留痕；ref_id 是工作流 run_uid 或会话 id。"""

    __tablename__ = "trigger_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trigger_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # running | waiting | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ref_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
