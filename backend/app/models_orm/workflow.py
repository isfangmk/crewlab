"""工作流持久化表：定义、运行、节点执行记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class Workflow(Base):
    """工作流的画布定义与版本。"""

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 画布定义（ADK 图结构）：nodes / edges / start
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # draft | published：画布改动一律落在 draft，只有发布才转 published
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # 发布版本号：只有发布才 +1，草稿保存不动它
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 最近一次发布的时间；NULL 表示从未发布过（首次发布沿用初始版本号）
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次保存时的校验结果："" 表示未校验；否则是校验错误信息
    validation_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WorkflowRun(Base):
    """一次工作流执行。"""

    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 对外展示与日志对照用的运行标识
    run_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # 运行时的定义快照：节点被删除后仍能回看这次运行
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # running | succeeded | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    triggered_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 运行时传入的输入（ADK START 的 node_input）
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowNodeRun(Base):
    """单个节点的执行记录（ADK 静态图节点每次执行一行）。"""

    __tablename__ = "workflow_node_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # ADK 节点类型：agent | function | join | start
    node_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 同一节点在图里第几次执行（循环节点会大于 1）
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # succeeded | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="succeeded")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 条件分支 / 循环节点选中的 route；其他节点为空
    route: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
