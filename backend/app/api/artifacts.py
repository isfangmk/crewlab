"""会话 Artifact 的列表与下发。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import artifact_service

router = APIRouter(tags=["artifacts"])


def _content_disposition(filename: str) -> str:
    """RFC 5987 形式的下载文件名头。

    HTTP 头只允许 Latin-1，中文文件名直接塞进 filename="…" 会 500；
    前端 parseContentDisposition 已按 filename*=utf-8'' 解析。
    """

    return f"attachment; filename*=utf-8''{quote(filename)}"


@router.get("/chat/sessions/{session_id}/artifacts")
async def list_session_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """会话内全部产出物（Artifacts 侧栏数据源），按时间正序。"""

    rows = await artifact_service.list_by_session(db, session_id)
    return {"items": [artifact_service.artifact_to_wire(r) for r in rows]}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> Response:
    """下发产出物：文件给原始字节，图表/表格给 JSON 文本。"""

    try:
        row = await artifact_service.get_artifact(db, artifact_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if row.kind == "file":
        if row.data_bytes is None:
            raise HTTPException(status_code=404, detail="文件内容已丢失")
        return Response(
            content=row.data_bytes,
            media_type=row.mime or "application/octet-stream",
            headers={"Content-Disposition": _content_disposition(row.file_name or row.id)},
        )

    filename = (row.title or row.id) + ".json"
    return Response(
        content=row.payload_json or "{}",
        media_type="application/json",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """单个产出物的结构化字段（前端预览用）。"""

    try:
        row = await artifact_service.get_artifact(db, artifact_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return artifact_service.artifact_to_wire(row)


@router.get("/artifacts/{artifact_id}/payload")
async def get_artifact_payload(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> Response:
    """产出物的 payload 原文（JSON 文本），前端渲染图表时直接取。"""

    try:
        row = await artifact_service.get_artifact(db, artifact_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=row.payload_json or "{}",
        media_type="application/json",
    )
