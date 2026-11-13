"""知识库：文档 CRUD、切块嵌入、向量检索。"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from app.models_orm.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services.embedding_client import embed_text, embed_texts
from app.services.knowledge_scope import KnowledgeScope, path_condition, scope_condition

logger = logging.getLogger(__name__)

# 按字符切块默认值；可被 system_settings(knowledge) 覆盖
_DEFAULT_CHUNK_SIZE = 800
_DEFAULT_CHUNK_OVERLAP = 100
_RRF_K = 60


@dataclass(frozen=True)
class KnowledgeHit:
    """一条检索命中：文档、RRF 得分、命中的切块与分道信息。"""

    doc: KnowledgeDoc
    score: float
    chunk_index: int
    content: str
    # 向量分道余弦相似度；仅关键词命中时为 None
    vector_score: float | None
    # 关键词分道内的排名（1 起）；仅向量命中时为 None
    keyword_rank: int | None


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
        "enabled": doc.enabled,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
        # 上传原件信息（手动 / 内置文档为 None）与检索命中累计
        "source_filename": doc.source_filename,
        "source_mime": doc.source_mime,
        "source_bytes": doc.source_bytes,
        "hit_count": doc.hit_count,
        "last_hit_at": doc.last_hit_at.isoformat() if doc.last_hit_at is not None else None,
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
    builtin: bool | None = None,
    q: str | None = None,
    sort_dir: str = "desc",
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[KnowledgeDoc], int]:
    """按筛选条件分页返回文档列表与命中总数。

    limit 为 None 时返回全部（供目录树聚合用）；前端列表必须传 limit，
    否则文档量级增长后前端会一次拉回全量。
    """

    filters = []
    if source_type is not None:
        filters.append(KnowledgeDoc.source_type == source_type)
    if path is not None:
        filters.append(KnowledgeDoc.path == path)
    if path_prefix is not None:
        # 本级或子级：与 knowledge_scope.path_condition 同口径，
        # 避免 `网络` 误命中 `网络安全`
        filters.append(path_condition(path_prefix))
    if tag is not None:
        filters.append(KnowledgeDoc.tags.contains([tag]))
    if builtin is not None:
        filters.append(_source_condition(builtin))
    if q is not None:
        pattern = f"%{_escape_like(q)}%"
        filters.append(
            or_(
                KnowledgeDoc.title.ilike(pattern, escape="\\"),
                KnowledgeDoc.title_en.ilike(pattern, escape="\\"),
            )
        )

    total = await db.scalar(select(func.count()).select_from(KnowledgeDoc).where(*filters))
    # 更新时间排序；同一时刻按 id 兜底，保证顺序稳定可复现
    order = (
        KnowledgeDoc.updated_at.asc()
        if sort_dir == "asc"
        else KnowledgeDoc.updated_at.desc()
    )
    # 列表页不需要正文：延迟加载 content，避免每页把几十篇全文从 DB 拉进内存
    stmt = (
        select(KnowledgeDoc)
        .options(defer(KnowledgeDoc.content))
        .where(*filters)
        .order_by(order, KnowledgeDoc.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    logger.info("已列出文档 total=%s 返回=%s offset=%s", total, len(rows), offset)
    return rows, int(total or 0)


async def doc_list_stats(db: AsyncSession, doc_ids: list[str]) -> dict[str, dict]:
    """列表统计：一条 SQL 取齐切块数、最后索引时间、正文字符数与 UTF-8 字节数。

    长度在数据库侧算，正文不进应用内存；只处理当页文档，不做逐行 N+1。
    切块数为 0 表示正文尚未成功索引，列表据此给出「未索引」状态。
    """

    empty = {
        "chunk_count": 0,
        "indexed_at": None,
        "content_chars": 0,
        "content_bytes": 0,
    }
    stats: dict[str, dict] = {doc_id: dict(empty) for doc_id in doc_ids}
    if not doc_ids:
        return stats

    chunk_agg = (
        select(
            KnowledgeChunk.doc_id.label("doc_id"),
            func.count(KnowledgeChunk.id).label("chunk_count"),
            func.max(KnowledgeChunk.created_at).label("indexed_at"),
        )
        .where(KnowledgeChunk.doc_id.in_(doc_ids))
        .group_by(KnowledgeChunk.doc_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                KnowledgeDoc.id,
                func.char_length(KnowledgeDoc.content),
                func.octet_length(KnowledgeDoc.content),
                func.coalesce(chunk_agg.c.chunk_count, 0),
                chunk_agg.c.indexed_at,
            )
            .outerjoin(chunk_agg, chunk_agg.c.doc_id == KnowledgeDoc.id)
            .where(KnowledgeDoc.id.in_(doc_ids))
        )
    ).all()
    for doc_id, chars, bytes_, count, indexed_at in rows:
        stats[doc_id] = {
            "chunk_count": int(count or 0),
            "indexed_at": indexed_at.isoformat() if indexed_at is not None else None,
            "content_chars": int(chars or 0),
            "content_bytes": int(bytes_ or 0),
        }
    return stats


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符；不转义会让用户输入的 % 变成全表匹配。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    source_filename: str | None = None,
    source_mime: str | None = None,
    source_bytes: int | None = None,
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
        source_filename=source_filename,
        source_mime=source_mime,
        source_bytes=source_bytes,
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


async def set_doc_enabled(db: AsyncSession, doc_id: str, enabled: bool) -> KnowledgeDoc:
    """切换文档是否参与检索；只改开关，不重建切块。"""

    doc = await get_doc(db, doc_id)
    doc.enabled = enabled
    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(doc)
    logger.info("设置文档启停 doc_id=%s enabled=%s", doc.id, enabled)
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


async def list_paths(db: AsyncSession, builtin: bool | None = None) -> list[dict]:
    """按 path 聚合文档数，供目录树使用。

    builtin 与列表接口同口径：true 只聚合 vault，false 只聚合组织文档，
    否则侧边栏两棵树的计数会混在一起。
    """

    stmt = select(KnowledgeDoc.path, func.count())
    if builtin is not None:
        stmt = stmt.where(_source_condition(builtin))
    stmt = stmt.group_by(KnowledgeDoc.path).order_by(KnowledgeDoc.path)
    rows = (await db.execute(stmt)).all()
    return [{"path": path, "count": count} for path, count in rows]


async def count_docs(db: AsyncSession, builtin: bool | None = None) -> int:
    """按来源组统计文档总数；builtin 与列表接口同口径。"""

    stmt = select(func.count()).select_from(KnowledgeDoc)
    if builtin is not None:
        stmt = stmt.where(_source_condition(builtin))
    total = await db.scalar(stmt)
    return int(total or 0)


def _apply_doc_filters(
    stmt,
    *,
    path: str | None,
    path_prefix: str | None,
    tags: list[str] | None,
    scope: KnowledgeScope | None = None,
    source_type: str | None = None,
):
    if path is not None:
        stmt = stmt.where(KnowledgeDoc.path == path)
    if path_prefix is not None:
        # 本级或子级：与 list_docs 同口径，避免 `网络` 误命中 `网络安全`
        stmt = stmt.where(path_condition(path_prefix))
    if source_type is not None:
        stmt = stmt.where(KnowledgeDoc.source_type == source_type)
    if tags:
        for t in tags:
            stmt = stmt.where(KnowledgeDoc.tags.contains([t]))
    # 助理范围：与上面的 path/tags 过滤是 AND 关系
    scope_cond = scope_condition(scope)
    if scope_cond is not None:
        stmt = stmt.where(scope_cond)
    return stmt


def _source_condition(builtin: bool):
    """来源组过滤：内置 = vault，组织 = 其余来源（manual / upload / repo）。

    与前端侧边栏的分组口径一致，避免「在组织分组里检索却连通内置文档」。
    """

    return (
        KnowledgeDoc.source_type == "vault"
        if builtin
        else KnowledgeDoc.source_type != "vault"
    )


async def resolve_rrf_min_score(db: AsyncSession) -> float:
    """读取 RRF 最低分阈值；缺配置或非法值显式失败。"""

    from app.models_orm.llm import AppSetting

    row = await db.get(AppSetting, "knowledge.rrf_min_score")
    if row is None:
        raise ValueError("缺少 knowledge.rrf_min_score")
    try:
        value = float(row.value)
    except ValueError as exc:
        raise ValueError(f"knowledge.rrf_min_score 不是数字: {row.value}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("knowledge.rrf_min_score 必须 >= 0")
    return value


async def search_docs(
    db: AsyncSession,
    *,
    query: str,
    limit: int,
    path: str | None,
    path_prefix: str | None,
    tags: list[str] | None,
    scope: KnowledgeScope | None = None,
    builtin: bool | None = None,
    source_type: str | None = None,
) -> list[KnowledgeHit]:
    """混合检索：向量余弦 + 关键词 ILIKE，RRF 融合后聚合到文档。"""

    query = query.strip()
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
        .where(KnowledgeDoc.enabled.is_(True))
        .order_by(distance)
        .limit(pool)
    )
    if builtin is not None:
        vec_stmt = vec_stmt.where(_source_condition(builtin))
    vec_stmt = _apply_doc_filters(
        vec_stmt,
        path=path,
        path_prefix=path_prefix,
        tags=tags,
        scope=scope,
        source_type=source_type,
    )
    vec_rows = (await db.execute(vec_stmt)).all()

    kw_stmt = (
        select(KnowledgeChunk, KnowledgeDoc)
        .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
        .where(KnowledgeDoc.enabled.is_(True))
        # 与列表标题筛选同口径：% / _ / \ 是用户输入的字面量，不能变成 LIKE 通配
        .where(KnowledgeChunk.content.ilike(f"%{_escape_like(query)}%", escape="\\"))
        # 关键词分道没有相关性打分，固定按切块 id 排序保证排名可复现
        .order_by(KnowledgeChunk.id)
        .limit(pool)
    )
    if builtin is not None:
        kw_stmt = kw_stmt.where(_source_condition(builtin))
    kw_stmt = _apply_doc_filters(
        kw_stmt,
        path=path,
        path_prefix=path_prefix,
        tags=tags,
        scope=scope,
        source_type=source_type,
    )
    kw_rows = (await db.execute(kw_stmt)).all()

    # RRF 按文档聚合：每个通道只取该文档最优排名，避免长文档靠切块数量累加分数；
    # 同时保留该文档排序最靠前的命中块与分道信息
    docs: dict[str, KnowledgeDoc] = {}
    best_chunk: dict[str, KnowledgeChunk] = {}
    vector_score: dict[str, float] = {}
    vector_rank: dict[str, int] = {}
    keyword_rank: dict[str, int] = {}
    for rank, (chunk, doc, dist) in enumerate(vec_rows, start=1):
        docs[doc.id] = doc
        best_chunk.setdefault(doc.id, chunk)
        vector_score.setdefault(doc.id, 1.0 - float(dist))
        vector_rank.setdefault(doc.id, rank)
    for rank, (chunk, doc) in enumerate(kw_rows, start=1):
        docs[doc.id] = doc
        best_chunk.setdefault(doc.id, chunk)
        keyword_rank.setdefault(doc.id, rank)
    rrf: dict[str, float] = {}
    for doc_id in docs:
        score = 0.0
        if doc_id in vector_rank:
            score += 1.0 / (_RRF_K + vector_rank[doc_id])
        if doc_id in keyword_rank:
            score += 1.0 / (_RRF_K + keyword_rank[doc_id])
        rrf[doc_id] = score

    min_score = await resolve_rrf_min_score(db)
    ranked_ids = sorted(
        (i for i in rrf if rrf[i] >= min_score),
        key=lambda i: rrf[i],
        reverse=True,
    )[:limit]
    hits = [
        KnowledgeHit(
            doc=docs[i],
            score=rrf[i],
            chunk_index=best_chunk[i].chunk_index,
            content=best_chunk[i].content,
            vector_score=vector_score.get(i),
            keyword_rank=keyword_rank.get(i),
        )
        for i in ranked_ids
    ]
    logger.info(
        "知识库混合检索 q_len=%s limit=%s builtin=%s source=%s vec=%s kw=%s min_score=%s hits=%s top_score=%s",
        len(query),
        limit,
        builtin,
        source_type,
        len(vec_rows),
        len(kw_rows),
        min_score,
        len(hits),
        hits[0].score if hits else None,
    )
    return hits


async def record_search_hits(db: AsyncSession, doc_ids: list[str]) -> None:
    """累加检索命中次数与最近命中时间；一次检索对同一文档只计 1 次。

    只更新计数器，不触碰 updated_at，避免把「被检索过」误当成「正文被改过」。
    """

    unique = list(dict.fromkeys(doc_ids))
    if not unique:
        return
    await db.execute(
        update(KnowledgeDoc)
        .where(KnowledgeDoc.id.in_(unique))
        .values(
            hit_count=KnowledgeDoc.hit_count + 1,
            last_hit_at=datetime.now(timezone.utc),
            # 显式带上 updated_at，阻止列级 onupdate 把「被检索」写成「正文被改」
            updated_at=KnowledgeDoc.updated_at,
        )
    )
    await db.commit()


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


async def search_for_agent(
    query: str,
    limit: int = 5,
    scope: KnowledgeScope | None = None,
) -> dict:
    """供 ADK Tool 调用：自建短会话检索并返回可引用片段。

    scope 由助理的知识范围决定；为空表示继承全部知识库。
    """

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
            scope=scope,
        )
        items = [
            {
                "doc_id": hit.doc.id,
                "title": hit.doc.title,
                "path": hit.doc.path,
                "chunk_index": hit.chunk_index,
                "score": hit.score,
                "excerpt": hit.content,
            }
            for hit in hits
        ]
        # 助理检索同样是「被用到」：与页面检索共用一个计数器，避免命中次数系统性偏低。
        # 用本次自建的短会话写，失败只记日志，不让统计影响检索结果。
        if items:
            try:
                await record_search_hits(db, [i["doc_id"] for i in items])
            except Exception:
                logger.warning("助理检索命中统计失败", exc_info=True)
        return {"items": items, "total": len(items)}
