"""会话内 Artifact 的落库、查询与下发。

产出物由代码解释器等工具执行产生；落库器（chat_service 的 tool_end 分支）
从工具侧的进程内暂存区取走 payload 后调用本服务。
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact
from app.tools.code_interpreter import take_pending, validate_artifacts

logger = logging.getLogger(__name__)


def artifact_to_wire(row: Artifact) -> dict[str, Any]:
    """对外字段：payload 还原成结构化对象。"""

    payload: Any = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            payload = None
    return {
        "id": row.id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "tool_call_id": row.tool_call_id,
        "kind": row.kind,
        "title": row.title,
        "mime": row.mime,
        "payload": payload,
        "file_name": row.file_name,
        "size": len(row.data_bytes) if row.data_bytes else 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def save_from_tool_result(
    db: AsyncSession,
    *,
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    message_id: str | None = None,
) -> list[Artifact]:
    """tool_end 时取走工具暂存的产出物并落库；返回落库后的行（含 id）。

    message_id 缺省按工具卡命名规则推导，保证截断/重发能精确清理。
    """

    pending = take_pending(tool_call_id)
    if not pending:
        return []
    validate_artifacts(pending)
    owner_message_id = message_id if message_id is not None else f"tool-card-{tool_call_id}"
    rows: list[Artifact] = []
    for index, item in enumerate(pending):
        kind = str(item.get("kind", "text"))
        payload = item.get("payload")
        data_bytes: bytes | None = None
        payload_json = ""
        file_name = ""
        if kind == "file":
            if not isinstance(payload, dict):
                raise ValueError("Artifact 文件载荷必须是对象")
            # base64 里可能带换行（用户代码按行编码），先剥离再校验
            b64 = "".join(str(payload.get("data", "")).split())
            try:
                data_bytes = base64.b64decode(b64, validate=True)
            except Exception as exc:  # noqa: BLE001 — 文件内容损坏时显式失败
                raise ValueError(f"Artifact 文件内容不是合法 base64: {exc}") from exc
            payload_json = ""
            file_name = str(payload.get("name", ""))[:256]
        else:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        rows.append(
            Artifact(
                id=str(uuid.uuid4()),
                session_id=session_id or None,
                message_id=owner_message_id[:128],
                tool_call_id=tool_call_id,
                seq=index,
                kind=kind,
                title=str(item.get("title", ""))[:256],
                mime=str(item.get("mime", "application/json"))[:128],
                payload_json=payload_json,
                data_bytes=data_bytes,
                file_name=file_name,
            )
        )
    db.add_all(rows)
    await db.flush()
    logger.info(
        "已保存 Artifacts session_id=%s tool=%s count=%s",
        session_id,
        tool_name,
        len(rows),
    )
    return rows


def tool_result_update(rows: list[Artifact]) -> dict[str, Any]:
    """给 tool_card result 的瘦身载荷：chart/table 带渲染 payload，file 只带引用。"""

    return {
        "artifacts": [
            {
                "id": row.id,
                "kind": row.kind,
                "title": row.title,
                "mime": row.mime,
                "payload": json.loads(row.payload_json) if row.payload_json else None,
                "file_name": row.file_name,
                "size": len(row.data_bytes) if row.data_bytes else 0,
            }
            for row in rows
        ]
    }


async def list_by_session(db: AsyncSession, session_id: str) -> list[Artifact]:
    """按会话取全部产出物（Artifacts 侧栏数据源），按创建时间与执行内序号正序。"""

    rows = await db.scalars(
        select(Artifact)
        .where(Artifact.session_id == session_id)
        .order_by(Artifact.created_at.asc(), Artifact.seq.asc(), Artifact.id.asc())
    )
    return list(rows.all())


async def get_artifact(db: AsyncSession, artifact_id: str) -> Artifact:
    """取单个产出物；不存在显式失败。"""

    row = await db.get(Artifact, artifact_id)
    if row is None:
        raise LookupError(f"Artifact 不存在: {artifact_id}")
    return row


def get_bytes(row: Artifact) -> tuple[bytes, str, str]:
    """下载内容：返回 (字节, media_type, 文件名)。"""

    if row.kind == "file":
        if row.data_bytes is None:
            raise LookupError("文件内容已丢失")
        return (
            row.data_bytes,
            row.mime or "application/octet-stream",
            row.file_name or row.id,
        )
    return (
        (row.payload_json or "{}").encode("utf-8"),
        "application/json",
        f"{row.title or row.id}.json",
    )


async def delete_for_messages(
    db: AsyncSession,
    *,
    session_id: str,
    message_ids: list[str],
    tool_call_ids: list[str],
) -> int:
    """删除消息对应的产出物（截断/编辑重发时调用）；兼容旧的空 message_id。"""

    conditions = []
    if message_ids:
        conditions.append(Artifact.message_id.in_(message_ids))
    if tool_call_ids:
        conditions.append(Artifact.tool_call_id.in_(tool_call_ids))
    if not conditions:
        return 0
    result = await db.execute(
        delete(Artifact).where(
            Artifact.session_id == session_id,
            or_(*conditions),
        )
    )
    rowcount = getattr(result, "rowcount", 0)
    count = int(rowcount) if rowcount is not None and rowcount > 0 else 0
    logger.info(
        "已删除消息关联 Artifacts session_id=%s messages=%s tools=%s count=%s",
        session_id,
        len(message_ids),
        tool_call_ids,
        count,
    )
    return count


async def copy_for_prefix(
    db: AsyncSession,
    *,
    source_session_id: str,
    target_session_id: str,
    message_id_map: dict[str, str],
    tool_call_ids: list[str],
) -> int:
    """分叉会话时复制前缀消息关联的产出物，并挂到新消息行上。"""

    conditions = []
    if message_id_map:
        conditions.append(Artifact.message_id.in_(list(message_id_map.keys())))
    if tool_call_ids:
        conditions.append(Artifact.tool_call_id.in_(tool_call_ids))
    if not conditions:
        return 0
    rows = await db.scalars(
        select(Artifact).where(
            Artifact.session_id == source_session_id,
            or_(*conditions),
        )
    )
    copies: list[Artifact] = []
    for row in rows.all():
        copies.append(
            Artifact(
                id=str(uuid.uuid4()),
                session_id=target_session_id,
                message_id=message_id_map.get(row.message_id, "")[:128],
                tool_call_id=row.tool_call_id,
                seq=row.seq,
                kind=row.kind,
                title=row.title,
                mime=row.mime,
                payload_json=row.payload_json,
                data_bytes=row.data_bytes,
                file_name=row.file_name,
            )
        )
    if copies:
        db.add_all(copies)
        await db.flush()
    logger.info(
        "分叉复制 Artifacts source=%s target=%s count=%s",
        source_session_id,
        target_session_id,
        len(copies),
    )
    return len(copies)
