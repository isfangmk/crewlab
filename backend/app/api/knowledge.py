"""知识库文档 CRUD / 上传 / 检索 API。"""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import SessionLocal, get_db
from app.schemas.knowledge import DocCreateIn, DocEnabledIn, DocMoveIn, DocUpdateIn, ScopePreviewIn
from app.services import knowledge_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.knowledge_scope import parse_scope, scope_preview

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge"])

_ALLOWED_UPLOAD_SUFFIX = (".md", ".txt", ".pdf", ".docx")

# 检索可按来源收口；与文档入库时的 source_type 取值集合一致
DOC_SOURCE_TYPES = frozenset({"manual", "upload", "vault", "repo"})


async def _record_search_hits_async(doc_ids: list[str]) -> None:
    """命中统计在响应发送后执行：不增加检索响应时间，写失败只记日志。"""

    if not doc_ids:
        return
    try:
        async with SessionLocal() as db:
            await knowledge_service.record_search_hits(db, doc_ids)
    except Exception:
        logger.warning("记录检索命中失败", exc_info=True)


@router.get("/knowledge/docs")
async def list_docs(
    source_type: str | None = None,
    path: str | None = None,
    path_prefix: str | None = None,
    tag: str | None = None,
    # 来源组：与前端侧边栏分组一致，builtin=true 只取 vault，false 只取组织文档
    builtin: bool | None = Query(None),
    # 标题关键字
    q: str | None = Query(None, max_length=200),
    sort_dir: str = Query("desc"),
    limit: int | None = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """文档列表：服务端筛选 + 分页，返回 items 与命中总数。

    limit 缺省时返回全部，供左侧目录树聚合使用；前端列表必须显式传 limit。
    """

    if source_type is not None and source_type not in DOC_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的 source_type: {source_type}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="sort_dir 必须是 asc 或 desc")

    items, total = await knowledge_service.list_docs(
        db,
        source_type=source_type,
        path=path,
        path_prefix=path_prefix,
        tag=tag,
        builtin=builtin,
        q=q.strip() if q and q.strip() else None,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    stats = await knowledge_service.doc_list_stats(db, [d.id for d in items])
    return {
        "items": [
            {
                **knowledge_service.doc_to_wire(d, include_content=False),
                # 统计值全部在库侧算好，正文既不进内存也不回传
                "chunk_count": stats[d.id]["chunk_count"],
                "content_chars": stats[d.id]["content_chars"],
                "content_bytes": stats[d.id]["content_bytes"],
                "indexed_at": stats[d.id]["indexed_at"],
            }
            for d in items
        ],
        "total": total,
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
    user: AuthUser = Depends(require_not_viewer),
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
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_create",
        resource_type="knowledge_doc",
        resource_id=doc.id,
        detail={"title": doc.title, "source_type": doc.source_type, "path": doc.path},
    )
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.patch("/knowledge/docs/{doc_id}")
async def update_doc(
    doc_id: str,
    body: DocUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
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
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_update",
        resource_type="knowledge_doc",
        resource_id=doc.id,
        detail={"title": doc.title, "path": doc.path},
    )
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.patch("/knowledge/docs/{doc_id}/enabled")
async def set_doc_enabled(
    doc_id: str,
    body: DocEnabledIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """切换文档是否参与检索；停用不删切块。"""

    try:
        doc = await knowledge_service.set_doc_enabled(db, doc_id, body.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_toggle",
        resource_type="knowledge_doc",
        resource_id=doc.id,
        detail={"title": doc.title, "enabled": doc.enabled},
    )
    return knowledge_service.doc_to_wire(doc, include_content=False)


@router.patch("/knowledge/docs/{doc_id}/move")
async def move_doc(
    doc_id: str,
    body: DocMoveIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        doc = await knowledge_service.move_doc(db, doc_id, body.path)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_move",
        resource_type="knowledge_doc",
        resource_id=doc.id,
        detail={"title": doc.title, "path": doc.path},
    )
    return knowledge_service.doc_to_wire(doc, include_content=False)


@router.delete("/knowledge/docs/{doc_id}")
async def delete_doc(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    try:
        # 先取标题：删除后行已消失，审计需要能辨认被删了哪篇。
        existing = await knowledge_service.get_doc(db, doc_id)
        title = existing.title
        await knowledge_service.delete_doc(db, doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_delete",
        resource_type="knowledge_doc",
        resource_id=doc_id,
        detail={"title": title},
    )


@router.post("/knowledge/upload")
async def upload_doc(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    path: str | None = Form(None),
    tags: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
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

    doc_title = (title or "").strip() or filename
    # 与新建表单同口径：去空白、丢空标签；尾逗号或中文逗号不应让整个上传失败
    tag_list: list[str] | None = None
    if tags is not None:
        tag_list = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]

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
            source_filename=filename,
            source_mime=file.content_type,
            source_bytes=len(raw),
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("上传文档 doc_id=%s filename=%s", doc.id, filename)
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.doc_upload",
        resource_type="knowledge_doc",
        resource_id=doc.id,
        detail={"title": doc.title, "filename": filename, "path": doc.path},
    )
    return knowledge_service.doc_to_wire(doc, include_content=True)


@router.post("/knowledge/reindex")
async def reindex_knowledge(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按当前切块配置重建全部文档索引。"""

    try:
        chunks = await knowledge_service.reindex_all_docs(db)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="knowledge.reindex",
        resource_type="knowledge",
        detail={"chunks": chunks},
    )
    return {"chunks": chunks}


@router.get("/knowledge/search")
async def search_knowledge(
    background_tasks: BackgroundTasks,
    q: str = Query(..., max_length=200),
    # 上限避免 limit * 4 的向量候选池被放大成重型查询
    limit: int = Query(10, ge=1, le=50),
    path: str | None = None,
    path_prefix: str | None = None,
    tag: list[str] | None = Query(None),
    scope_path: list[str] | None = Query(None),
    scope_doc: list[str] | None = Query(None),
    # 来源组：前端按侧边栏分组检索，不传 = 全库
    builtin: bool | None = Query(None),
    # 来源类型：组织文档里 manual / upload / repo 混排，前端按「来源」筛选收口
    source_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    query = q.strip()
    if query == "":
        raise HTTPException(status_code=400, detail="q 不能为空")
    if source_type is not None and source_type not in DOC_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的 source_type: {source_type}")
    try:
        scope = parse_scope(
            {"paths": scope_path or [], "doc_ids": scope_doc or []}
        )
        hits = await knowledge_service.search_docs(
            db,
            query=query,
            limit=limit,
            path=path,
            path_prefix=path_prefix,
            tags=tag,
            scope=scope,
            builtin=builtin,
            source_type=source_type,
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = {
        "items": [
            {
                "doc": knowledge_service.doc_to_wire(hit.doc, include_content=False),
                "score": hit.score,
                "vector_score": hit.vector_score,
                "keyword_rank": hit.keyword_rank,
                "chunk_index": hit.chunk_index,
                "excerpt": hit.content,
            }
            for hit in hits
        ],
        "total": len(hits),
    }
    # 命中统计放到响应之后：读接口不在关键路径上同步写库
    background_tasks.add_task(_record_search_hits_async, list({hit.doc.id for hit in hits}))
    return payload


@router.get("/knowledge/paths")
async def list_paths(
    builtin: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """目录树数据：各目录的文档数 + 该来源组的文档总数。"""

    items = await knowledge_service.list_paths(db, builtin=builtin)
    doc_count = await knowledge_service.count_docs(db, builtin=builtin)
    return {"items": items, "total": doc_count}


@router.post("/knowledge/scope-preview")
async def preview_scope(
    body: ScopePreviewIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """助理编辑器实时预览：命中多少篇、哪些目录/文档已不存在。"""

    try:
        scope = parse_scope({"paths": body.paths, "doc_ids": body.doc_ids})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await scope_preview(db, scope)
