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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.artifact import Artifact
from app.tools.code_interpreter import take_pending

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
) -> list[Artifact]:
    """tool_end 时取走工具暂存的产出物并落库；返回落库后的行（含 id）。"""

    pending = take_pending(tool_call_id)
    if not pending:
        return []
    rows: list[Artifact] = []
    for item in pending:
        kind = item.get("kind", "text")
        payload = item.get("payload")
        data_bytes: bytes | None = None
        payload_json = ""
        if kind == "file":
            # base64 里可能带换行（用户代码按行编码），先剥离再校验
            b64 = "".join(str(payload.get("data", "")).split())
            try:
                data_bytes = base64.b64decode(b64, validate=True)
            except Exception as exc:  # noqa: BLE001 — 文件内容损坏时显式失败
                raise ValueError(f"Artifact 文件内容不是合法 base64: {exc}") from exc
            payload_json = ""
        else:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        rows.append(
            Artifact(
                id=str(uuid.uuid4()),
                session_id=session_id or None,
                tool_call_id=tool_call_id,
                kind=kind,
                title=str(item.get("title", ""))[:256],
                mime=str(item.get("mime", "application/json"))[:128],
                payload_json=payload_json,
                data_bytes=data_bytes,
                file_name=str(payload.get("name", ""))[:256] if kind == "file" else "",
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
    """按会话取全部产出物（Artifacts 侧栏数据源），按创建时间正序。"""

    from sqlalchemy import select

    rows = await db.scalars(
        select(Artifact)
        .where(Artifact.session_id == session_id)
        .order_by(Artifact.created_at.asc(), Artifact.id.asc())
    )
    return list(rows.all())


async def get_artifact(db: AsyncSession, artifact_id: str) -> Artifact:
    """取单个产出物；不存在显式失败。"""

    row = await db.get(Artifact, artifact_id)
    if row is None:
        raise LookupError(f"Artifact 不存在: {artifact_id}")
    return row
