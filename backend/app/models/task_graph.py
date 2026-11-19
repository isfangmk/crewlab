"""统一任务图持久化表：一次图运行与各节点执行记录。

取代旧的 plan_executions/plan_steps 与 investigation_dispatches/
investigation_worker_runs 四张表；历史不迁移，由建表脚本删旧建新。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AwareDateTime, Base


class TaskGraphRun(Base):
    """一次任务图运行：主代理提交图后进入草稿，确认后展开成 DAG 执行。"""

    __tablename__ = "task_graph_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    # 对外标识：工具结果、事件订阅、API 全用它
    graph_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 来源会话：右栏按会话取图；会话删除则级联删除
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 对话折叠块所在消息行 id（tool_card 行），用于卡片与图互查
    card_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # 总目标：用户要解决的问题，随上下文传给每个节点
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # draft | running | paused | succeeded | failed | stopped
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # 提交的图定义快照（TaskGraphSpec.model_dump_json）+ 预算参数
    spec_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # 全局发现记录（含 n 与 retired 标记）；编号一经分配不复用
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class TaskGraphNode(Base):
    """单个任务节点：执行前可编辑，执行后落结论/要点/工具轨迹/用量。"""

    __tablename__ = "task_graph_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    __table_args__ = {"sqlite_autoincrement": True}
    graph_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("task_graph_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 事件路由标识（事件总线按它发 node_log）
    node_uid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # 图内局部 id（如 A/B/C）：用于渲染依赖连线
    local_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 前驱 local_id 列表：前端按此分层拓扑渲染
    depends_on_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 展示顺序（提交时声明顺序），仅用于稳定渲染
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 声明的工具清单（非空则按名收窄挂载面）
    tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 声明的技能 key 清单（节点执行时按声明装配；空 = 不装配技能）
    skills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # 可选：指定本节点复用的助理标识；空表示用通用节点人设
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # pending | running | succeeded | failed | skipped | stopped
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    findings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tool_trace_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
