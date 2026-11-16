"""规划执行的持久化表：一次计划与各步骤的执行记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class PlanExecution(Base):
    """一次规划执行：主代理经 create_plan 产出计划，用户确认后逐步执行。"""

    __tablename__ = "plan_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    # 对外标识：工具结果、右栏事件订阅与 API 全用它
    plan_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 来源会话：右栏按会话取计划；会话删除则计划级联删除
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # 总目标：用户要解决的问题，逐步执行时随上下文传给每一步
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # draft | running | paused | succeeded | failed | stopped
    # paused = 某步失败/用户暂停，等待重试/跳过/停止决定
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # 步骤预算与执行参数快照
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class PlanStep(Base):
    """计划步骤：执行前可编辑（排序/增删/改文案），执行后落结果摘要。"""

    __tablename__ = "plan_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("plan_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 单步的事件路由标识（事件总线按它发 step_log）
    step_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 展示顺序：编辑时整体重排，执行严格按此顺序
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 预估工具清单（纯展示，如 ["web_search"]）
    tools_hint: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # pending | running | succeeded | failed | skipped | stopped
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # 第几次执行：重试 +1，历史不保留
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 本步结论摘要（成功后回写；跳过时写占位说明）
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 本步要点（findings 围栏解析结果，带 call_id 证据）
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 执行日志节选（实时日志走事件总线）
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 本步用量；缺键不造假 0
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
