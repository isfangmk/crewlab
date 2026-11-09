"""知识库：文档 CRUD、切块嵌入、向量检索。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_orm.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services.embedding_client import embed_text, embed_texts

logger = logging.getLogger(__name__)

# 按字符切块默认值；可被 system_settings(knowledge) 覆盖
_DEFAULT_CHUNK_SIZE = 800
_DEFAULT_CHUNK_OVERLAP = 100
_RRF_K = 60


async def resolve_chunk_params(db: AsyncSession) -> tuple[int, int]:
    """从 AppSetting(knowledge.*) 读取切块参数；缺配置显式失败。"""

    from app.models_orm.llm import AppSetting

    size_row = await db.get(AppSetting, "knowledge.chunk_size")
    overlap_row = await db.get(AppSetting, "knowledge.chunk_overlap")
    if size_row is None:
        raise ValueError("缺少 knowledge.chunk_size")
    if overlap_row is None:
        raise ValueError("缺少 knowledge.chunk_overlap")
    size = int(size_row.value)
    overlap = int(overlap_row.value)
    if size < 64:
        raise ValueError("chunk_size 必须 >= 64")
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap 必须满足 0 <= overlap < chunk_size")
    return size, overlap


def _resolve_path(path: str | None) -> str:
    """契约：省略 path 表示根目录空串；传入则原样使用（禁止归一化清洗）。"""

    if path is None:
        return ""
    return path


def _resolve_tags(tags: list[str] | None) -> list[str]:
    """契约：省略 tags 表示无标签。"""

    if tags is None:
        return []
    for i, t in enumerate(tags):
        if not isinstance(t, str):
            raise TypeError(f"tags[{i}] 必须是字符串")
        if t == "":
            raise ValueError(f"tags[{i}] 不能为空")
    return tags


def chunk_text(
    content: str,
    *,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """按固定窗口切块；正文过短则单块。"""

    if content == "":
        raise ValueError("content 不能为空")
    if chunk_size < 64:
        raise ValueError("chunk_size 必须 >= 64")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须满足 0 <= overlap < chunk_size")
    if len(content) <= chunk_size:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = start + chunk_size
        piece = content[start:end]
        if piece == "":
            raise RuntimeError("切块结果为空")
        chunks.append(piece)
        if end >= len(content):
            break
        start = end - chunk_overlap
        if start < 0:
            raise RuntimeError("切块窗口计算错误")
    return chunks


def doc_to_wire(doc: KnowledgeDoc, *, include_content: bool) -> dict:
    """序列化为前端 KnowledgeDoc 形状。"""

    out: dict = {
        "id": doc.id,
        "source_type": doc.source_type,
        "title": doc.title,
        "path": doc.path,
        "tags": list(doc.tags),
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }
    if doc.title_en is not None:
        out["title_en"] = doc.title_en
    if doc.url is not None:
        out["url"] = doc.url
    if include_content:
        out["content"] = doc.content
    return out


async def _replace_chunks(db: AsyncSession, doc: KnowledgeDoc) -> int:
    """删除旧切块并重新嵌入写入。"""

    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
    size, overlap = await resolve_chunk_params(db)
    pieces = chunk_text(doc.content, chunk_size=size, chunk_overlap=overlap)
    logger.info(
        "切块完成 doc_id=%s chunks=%s content_len=%s size=%s overlap=%s",
        doc.id,
        len(pieces),
        len(doc.content),
        size,
        overlap,
    )
    vectors = await embed_texts(pieces)
    for i, (text, vec) in enumerate(zip(pieces, vectors, strict=True)):
        db.add(
            KnowledgeChunk(
                doc_id=doc.id,
                chunk_index=i,
                content=text,
                embedding=vec,
            )
        )
    return len(pieces)


async def list_docs(
    db: AsyncSession,
    *,
    source_type: str | None,
    path: str | None,
    path_prefix: str | None,
    tag: str | None,
) -> list[KnowledgeDoc]:
    stmt = select(KnowledgeDoc).order_by(KnowledgeDoc.updated_at.desc())
    if source_type is not None:
        stmt = stmt.where(KnowledgeDoc.source_type == source_type)
    if path is not None:
        stmt = stmt.where(KnowledgeDoc.path == path)
    if path_prefix is not None:
        stmt = stmt.where(KnowledgeDoc.path.startswith(path_prefix))
    if tag is not None:
        stmt = stmt.where(KnowledgeDoc.tags.contains([tag]))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_doc(db: AsyncSession, doc_id: str) -> KnowledgeDoc:
    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None:
        raise LookupError(f"文档不存在: {doc_id}")
    return doc


async def create_doc(
    db: AsyncSession,
    *,
    title: str,
    content: str,
    source_type: str,
    title_en: str | None,
    url: str | None,
    path: str | None,
    tags: list[str] | None,
) -> KnowledgeDoc:
    if source_type not in ("manual", "upload", "vault"):
        raise ValueError(f"不支持的 source_type: {source_type}")
    doc = KnowledgeDoc(
        id=str(uuid.uuid4()),
        source_type=source_type,
        title=title,
        title_en=title_en,
        content=content,
        path=_resolve_path(path),
        url=url,
        tags=_resolve_tags(tags),
    )
    db.add(doc)
    await db.flush()
    n = await _replace_chunks(db, doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(
        "创建文档 doc_id=%s source=%s title=%s path=%s chunks=%s",
        doc.id,
        doc.source_type,
        doc.title,
        doc.path,
        n,
    )
    return doc


async def update_doc(
    db: AsyncSession,
    doc_id: str,
    *,
    title: str,
    content: str,
    title_en: str | None,
    path: str | None,
    tags: list[str] | None,
) -> KnowledgeDoc:
    doc = await get_doc(db, doc_id)
    if doc.source_type == "vault":
        raise PermissionError("内置知识库文档只读，禁止修改")
    doc.title = title
    doc.title_en = title_en
    doc.content = content
    doc.path = _resolve_path(path)
    doc.tags = _resolve_tags(tags)
    doc.updated_at = datetime.now(timezone.utc)
    n = await _replace_chunks(db, doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("更新文档 doc_id=%s chunks=%s", doc.id, n)
    return doc


async def move_doc(db: AsyncSession, doc_id: str, path: str) -> KnowledgeDoc:
    doc = await get_doc(db, doc_id)
    if doc.source_type == "vault":
        raise PermissionError("内置知识库文档只读，禁止移动")
    doc.path = path
    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(doc)
    logger.info("移动文档 doc_id=%s path=%s", doc.id, doc.path)
    return doc


async def delete_doc(db: AsyncSession, doc_id: str) -> None:
    doc = await get_doc(db, doc_id)
    if doc.source_type == "vault":
        raise PermissionError("内置知识库文档只读，禁止删除")
    await db.delete(doc)
    await db.commit()
    logger.info("删除文档 doc_id=%s", doc_id)


async def list_paths(db: AsyncSession) -> list[dict]:
    """按 path 聚合文档数，供目录树使用。"""

    stmt = (
        select(KnowledgeDoc.path, func.count())
        .group_by(KnowledgeDoc.path)
        .order_by(KnowledgeDoc.path)
    )
    rows = (await db.execute(stmt)).all()
    return [{"path": path, "count": count} for path, count in rows]


def _apply_doc_filters(stmt, *, path: str | None, path_prefix: str | None, tags: list[str] | None):
    if path is not None:
        stmt = stmt.where(KnowledgeDoc.path == path)
    if path_prefix is not None:
        stmt = stmt.where(KnowledgeDoc.path.startswith(path_prefix))
    if tags:
        for t in tags:
            stmt = stmt.where(KnowledgeDoc.tags.contains([t]))
    return stmt


async def search_docs(
    db: AsyncSession,
    *,
    query: str,
    limit: int,
    path: str | None,
    path_prefix: str | None,
    tags: list[str] | None,
) -> list[tuple[KnowledgeDoc, float]]:
    """混合检索：向量余弦 + 关键词 ILIKE，RRF 融合后聚合到文档。"""

    if query == "":
        raise ValueError("q 不能为空")
    if limit < 1:
        raise ValueError("limit 必须 >= 1")

    pool = limit * 4
    query_vec = await embed_text(query)
    distance = KnowledgeChunk.embedding.cosine_distance(query_vec)
    vec_stmt = (
        select(KnowledgeChunk, KnowledgeDoc, distance.label("distance"))
        .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
        .order_by(distance)
        .limit(pool)
    )
    vec_stmt = _apply_doc_filters(
        vec_stmt, path=path, path_prefix=path_prefix, tags=tags
    )
    vec_rows = (await db.execute(vec_stmt)).all()

    kw_stmt = (
        select(KnowledgeChunk, KnowledgeDoc)
        .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
        .where(KnowledgeChunk.content.ilike(f"%{query}%"))
        .limit(pool)
    )
    kw_stmt = _apply_doc_filters(kw_stmt, path=path, path_prefix=path_prefix, tags=tags)
    kw_rows = (await db.execute(kw_stmt)).all()

    # RRF：按文档聚合排名分
    rrf: dict[str, float] = {}
    docs: dict[str, KnowledgeDoc] = {}
    for rank, (_chunk, doc, _dist) in enumerate(vec_rows, start=1):
        docs[doc.id] = doc
        rrf[doc.id] = rrf.get(doc.id, 0.0) + 1.0 / (_RRF_K + rank)
    for rank, (_chunk, doc) in enumerate(kw_rows, start=1):
        docs[doc.id] = doc
        rrf[doc.id] = rrf.get(doc.id, 0.0) + 1.0 / (_RRF_K + rank)

    ranked_ids = sorted(rrf.keys(), key=lambda i: rrf[i], reverse=True)[:limit]
    ranked = [(docs[i], rrf[i]) for i in ranked_ids]
    logger.info(
        "知识库混合检索 q_len=%s limit=%s vec=%s kw=%s hits=%s top_score=%s",
        len(query),
        limit,
        len(vec_rows),
        len(kw_rows),
        len(ranked),
        ranked[0][1] if ranked else None,
    )
    return ranked


async def reindex_all_docs(db: AsyncSession) -> int:
    """按当前切块配置重建全部文档切块。"""

    docs = list((await db.scalars(select(KnowledgeDoc))).all())
    total = 0
    for doc in docs:
        n = await _replace_chunks(db, doc)
        total += n
    await db.commit()
    logger.info("知识库全量重建切块 docs=%s chunks=%s", len(docs), total)
    return total


def extract_upload_text(*, filename: str, raw: bytes) -> str:
    """按扩展名解析上传字节为纯文本；不支持的类型显式失败。"""

    lower = filename.lower()
    if lower.endswith(".md") or lower.endswith(".txt"):
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件必须是 UTF-8") from exc
        if content == "":
            raise ValueError("文件内容为空")
        return content
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text is None:
                continue
            if text != "":
                parts.append(text)
        content = "\n".join(parts)
        if content == "":
            raise ValueError("PDF 未解析出文本")
        return content
    if lower.endswith(".docx"):
        import io

        from docx import Document

        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text != ""]
        content = "\n".join(parts)
        if content == "":
            raise ValueError("DOCX 未解析出文本")
        return content
    raise ValueError("仅支持 .md / .txt / .pdf / .docx")


async def search_for_agent(query: str, limit: int = 5) -> dict:
    """供 ADK Tool 调用：自建短会话检索并返回可引用片段。"""

    from app.db import SessionLocal

    if query == "":
        raise ValueError("query 不能为空")
    if limit < 1 or limit > 20:
        raise ValueError("limit 范围是 1..20")

    async with SessionLocal() as db:
        hits = await search_docs(
            db,
            query=query,
            limit=limit,
            path=None,
            path_prefix=None,
            tags=None,
        )
        items = []
        for doc, score in hits:
            # 取该文档第一块作为引用摘要；无切块则失败
            result = await db.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.doc_id == doc.id)
                .order_by(KnowledgeChunk.chunk_index)
                .limit(1)
            )
            chunk = result.scalar_one_or_none()
            if chunk is None:
                raise RuntimeError(f"文档缺少切块 doc_id={doc.id}")
            items.append(
                {
                    "doc_id": doc.id,
                    "title": doc.title,
                    "path": doc.path,
                    "score": score,
                    "excerpt": chunk.content,
                }
            )
        return {"items": items, "total": len(items)}
