"""会话图片附件：上传 / 读取 / 列表。"""

from __future__ import annotations

import base64
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.attachment import ChatAttachment

logger = logging.getLogger(__name__)

# 仅允许图片 MIME；与前端 ChatInput 的 ACCEPTED_IMAGE_TYPES 对齐
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def attachment_to_wire(row: ChatAttachment) -> dict:
    """附件对外字段；不含 data_url（体积大，单独按 id 取）。"""

    return {
        "id": row.id,
        "name": row.name,
        "mime_type": row.mime_type,
        "size": row.size,
        "created_at": row.created_at.isoformat(),
    }


def bytes_from_attachment(row: ChatAttachment) -> bytes:
    """从 data_url 解出原始字节；mime 必须与行内记录一致。"""

    prefix = f"data:{row.mime_type};base64,"
    if not row.data_url.startswith(prefix):
        raise ValueError(
            f"附件 data_url 与 mime 不一致 attachment_id={row.id} mime={row.mime_type}"
        )
    return base64.b64decode(row.data_url[len(prefix) :], validate=True)


def _validate_mime_type(mime_type: str) -> None:
    if not isinstance(mime_type, str) or mime_type == "":
        raise ValueError("缺少文件类型")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"不支持的文件类型: {mime_type}（仅支持 "
            + " / ".join(sorted(ALLOWED_MIME_TYPES))
            + "）"
        )


async def create_attachment(
    db: AsyncSession,
    *,
    session_id: str,
    name: str,
    mime_type: str,
    data: bytes,
) -> ChatAttachment:
    """保存一条图片附件。

    会话不存在必须失败；附件 id 供发消息时通过 attachment_ids 引用。
    """

    from app.services.chat_service import get_session

    await get_session(db, session_id)
    if not isinstance(name, str) or name == "":
        raise ValueError("缺少文件名")
    _validate_mime_type(mime_type)
    if len(data) == 0:
        raise ValueError("文件内容为空")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"文件超过上限: {len(data)} > {MAX_ATTACHMENT_BYTES} 字节"
        )

    encoded = base64.b64encode(bytes(data)).decode("ascii")
    row = ChatAttachment(
        id=str(uuid4()),
        session_id=session_id,
        name=name,
        mime_type=mime_type,
        size=len(data),
        data_url=f"data:{mime_type};base64,{encoded}",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "保存会话附件 id=%s session_id=%s name=%s mime=%s size=%s",
        row.id,
        session_id,
        name,
        mime_type,
        row.size,
    )
    return row


async def get_attachment(
    db: AsyncSession, session_id: str, attachment_id: str
) -> ChatAttachment:
    row = await db.get(ChatAttachment, attachment_id)
    if row is None or row.session_id != session_id:
        raise LookupError(f"附件不存在: {attachment_id}")
    return row


async def list_attachments(
    db: AsyncSession, session_id: str
) -> list[ChatAttachment]:
    result = await db.scalars(
        select(ChatAttachment)
        .where(ChatAttachment.session_id == session_id)
        .order_by(ChatAttachment.created_at.asc())
    )
    return list(result.all())
