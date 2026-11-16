"""并行子代理排查的持久化表：一次排查派发与各路 worker 运行记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class InvestigationDispatch(Base):
    """一次并行排查：主代理经 dispatch_investigation 派发的 N 路 worker 编排。"""

    __tablename__ = "investigation_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    # 对外标识：工具结果、右栏事件订阅与 API 全用它
    dispatch_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 来源会话：右栏工作台按会话取排查；会话删除则排查记录级联删除
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 对话折叠块所在消息行 id（tool_card 行），用于卡片与排查互查
    card_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # running | partial | succeeded | failed | stopped
    # partial = 已出初步汇总但仍有 worker 在后台续跑
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    # 派发配置快照：worker 规格与预算参数
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 全量发现记录（含全局编号 n 与 retired 标记）：编号一经分配不复用
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 总预算与消耗；缺键不造假 0
    budget_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class InvestigationWorkerRun(Base):
    """单路 worker 的执行记录；重试原地更新同一行并递增 attempt。"""

    __tablename__ = "investigation_worker_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    dispatch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("investigation_dispatches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 单路 worker 的事件路由标识（事件总线按它发 worker_log）
    worker_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 角色名与排查目标（如「指标员 / 关联指标趋势与异常窗口」）
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 第几次执行：重试 +1，历史不保留（面板只展示当前）
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # queued | running | succeeded | failed | stopped
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # 执行日志节选（实时日志走事件总线，落库只存汇总节选）
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 本路发现（{conclusion, findings}；编号 n 在 dispatch.findings_json 里全局分配）
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 本路预算与消耗；缺键不造假 0
    budget_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
