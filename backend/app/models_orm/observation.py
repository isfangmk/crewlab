"""对话回合可观测记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class ObservationTurn(Base):
    """一轮对话的 Trace：延迟、token、错误、会话关联。"""

    __tablename__ = "observation_turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # running | ok | error | stopped
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    input_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 本回合调用过的工具 / 技能数量，列表页直接展示，避免逐行聚合
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skill_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ObservationToolCall(Base):
    """回合内的单次工具 / 技能调用。"""

    __tablename__ = "observation_tool_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # 工具线名；技能调用为 load_skill 等
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # skill | mcp | tool
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 技能名 / MCP server 名等附加标识，普通工具为空
    detail: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # running | ok | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
