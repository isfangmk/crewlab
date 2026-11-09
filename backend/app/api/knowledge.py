"""知识库文档 CRUD / 上传 / 检索 API。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.schemas.knowledge import DocCreateIn, DocMoveIn, DocUpdateIn
from app.services import knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge"])

_ALLOWED_UPLOAD_SUFFIX = (".md", ".txt", ".pdf", ".docx")


@router.get("/knowledge/docs")
async def list_docs(
    source_type: str | None = None,
    path: str | None = None,
    path_prefix: str | None = None,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await knowledge_service.list_docs(
        db,
        source_type=source_type,
        path=path,
        path_prefix=path_prefix,
        tag=tag,
    )
    return {
        "items": [knowledge_service.doc_to_wire(d, include_content=False) for d in items],
        "total": len(items),
    }


@router.get("/knowledge/docs/{doc_id}")
async def get_doc(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        doc = await knowledge_service.get_doc(db, doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.post("/knowledge/docs")
async def create_doc(
    body: DocCreateIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        doc = await knowledge_service.create_doc(
            db,
            title=body.title,
            content=body.content,
            source_type="manual",
            title_en=body.title_en,
            url=body.url,
            path=body.path,
            tags=body.tags,
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.patch("/knowledge/docs/{doc_id}")
async def update_doc(
    doc_id: str,
    body: DocUpdateIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        doc = await knowledge_service.update_doc(
            db,
            doc_id,
            title=body.title,
            content=body.content,
            title_en=body.title_en,
            path=body.path,
            tags=body.tags,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.patch("/knowledge/docs/{doc_id}/move")
async def move_doc(
    doc_id: str,
    body: DocMoveIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        doc = await knowledge_service.move_doc(db, doc_id, body.path)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return knowledge_service.doc_to_wire(doc, include_content=False)


@router.delete("/knowledge/docs/{doc_id}")
async def delete_doc(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> None:
    try:
        await knowledge_service.delete_doc(db, doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/knowledge/upload")
async def upload_doc(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    path: str | None = Form(None),
    tags: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """multipart 上传 .md/.txt/.pdf/.docx，入库为 source_type=upload。"""

    filename = file.filename
    if filename is None or filename == "":
        raise HTTPException(status_code=400, detail="缺少文件名")
    lower = filename.lower()
    if not lower.endswith(_ALLOWED_UPLOAD_SUFFIX):
        raise HTTPException(status_code=400, detail="仅支持 .md / .txt / .pdf / .docx")

    raw = await file.read()
    try:
        content = knowledge_service.extract_upload_text(filename=filename, raw=raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    doc_title = title if title is not None else filename
    tag_list: list[str] | None = None if tags is None else tags.split(",")

    try:
        doc = await knowledge_service.create_doc(
            db,
            title=doc_title,
            content=content,
            source_type="upload",
            title_en=None,
            url=None,
            path=path,
            tags=tag_list,
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("上传文档 doc_id=%s filename=%s", doc.id, filename)
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.post("/knowledge/reindex")
async def reindex_knowledge(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """按当前切块配置重建全部文档索引。"""

    try:
        chunks = await knowledge_service.reindex_all_docs(db)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"chunks": chunks}


@router.get("/knowledge/search")
async def search_knowledge(
    q: str = Query(...),
    limit: int = Query(10),
    path: str | None = None,
    path_prefix: str | None = None,
    tag: list[str] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        hits = await knowledge_service.search_docs(
            db,
            query=q,
            limit=limit,
            path=path,
            path_prefix=path_prefix,
            tags=tag,
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": [
            {
                "doc": knowledge_service.doc_to_wire(doc, include_content=False),
                "score": score,
            }
            for doc, score in hits
        ],
        "total": len(hits),
    }


@router.get("/knowledge/paths")
async def list_paths(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await knowledge_service.list_paths(db)
    return {"items": items, "total": len(items)}
