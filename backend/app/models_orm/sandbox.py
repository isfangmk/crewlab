"""沙盒执行记录：每次沙盒里跑过的代码都留一行。

记录的目的不是「跑过就行」，而是事后能回答三个问题：
这段代码是谁触发的（技能脚本 / 工作流节点 / 手动试跑）、它实际输出了什么、
失败是代码自己的错还是沙盒环境出了问题。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class SandboxExecution(Base):
    """一次沙盒代码执行的记录。"""

    __tablename__ = "sandbox_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # skill | workflow | manual：谁触发的这次执行
    source: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 触发方的具体标识：技能名 / 工作流节点 id / 试跑标识
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 关联的业务对象：会话 id / 工作流运行 id，便于从业务侧回查
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    # 执行的语言与镜像：镜像变了行为可能就变了，必须留档
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="python")
    image: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # 沙盒是否允许联网：默认关闭，开了也要能看出这次开了
    network_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    # 提交给沙盒的代码正文（截断后落库）
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 标准输出 / 标准错误（截断后落库）
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 进程退出码；沙盒自身故障时为 None
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # succeeded | failed | timeout
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 失败原因：异常类型与消息，或超时说明
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
