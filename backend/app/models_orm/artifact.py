"""会话内产出的 Artifact：图表 / 表格 / 文件。

来源是对话工具（如代码解释器）的执行产出；payload 或字节都随会话级联删除。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models_orm.base import Base


class Artifact(Base):
    """一次工具执行的结构化产出物。"""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    # 产出物所属的消息行（工具卡 id）；用于截断/重发时精确删除
    message_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", index=True
    )
    # 同一次工具执行内的产出顺序；时间戳精度不足时保证列表稳定
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # chart | table | file | text
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    mime: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    # chart/table/text 的结构化内容（JSON 文本）；file 为空
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # file 的原始字节；chart/table 为空
    data_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
