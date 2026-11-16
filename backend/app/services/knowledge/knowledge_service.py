"""知识库：文档 CRUD、切块嵌入、向量检索。

向量读写统一走 app.vector_store（可配置实现）；
切块正文与文档属性在本库（业务数据库），检索时按 ref_id join 回来。
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    LargeBinary,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.config import get_settings
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.services.knowledge.knowledge_scope import KnowledgeScope, path_condition, scope_condition
from app.services.platform.embedding_client import embed_text, embed_texts
from app.vector_store import (
    KNOWLEDGE_NAMESPACE,
    get_vector_store,
    raise_vector_store_unavailable,
)

logger = logging.getLogger(__name__)

# 按字符切块默认值；可被 system_settings(knowledge) 覆盖
_DEFAULT_CHUNK_SIZE = 800
_DEFAULT_CHUNK_OVERLAP = 100
_RRF_K = 60
# 向量相似度下限：低于此值是噪声而非语义命中，直接丢弃——
# RRF 只按排名计分，管不住无命中时的噪声凑数（本嵌入模型噪声 ≤0.47、真命中 ≥0.59）
_VECTOR_MIN_SIMILARITY = 0.5

# 由外部源同步维护的文档：内容随同步刷新，不开放站内编辑/移动
EXTERNAL_SOURCE_TYPES = frozenset({"repo", "url", "git"})


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

    from app.models.llm import AppSetting

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
        # 外部源归属；手工 / 上传 / 内置文档为 None
        "external_source_id": doc.external_source_id,
        "external_key": doc.external_key,
    }
    if doc.title_en is not None:
        out["title_en"] = doc.title_en
    if doc.url is not None:
        out["url"] = doc.url
    if include_content:
        out["content"] = doc.content
    return out


async def _replace_chunks(db: AsyncSession, doc: KnowledgeDoc) -> int:
    """删除旧切块并重新嵌入写入；向量写入向量存储，正文写业务库。

    失败顺序：切块/嵌入先于任何写操作，嵌入服务不可用时业务库原样保留。
    """

    vs = get_vector_store()
    if vs is None:
        raise_vector_store_unavailable()
    old_ids: list[int] = list(
        (await db.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.doc_id == doc.id))).all()
    )
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
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
    rows: list[KnowledgeChunk] = []
    for i, piece in enumerate(pieces):
        rows.append(KnowledgeChunk(doc_id=doc.id, chunk_index=i, content=piece))
    db.add_all(rows)
    await db.flush()
    # 先删旧向量再写新向量：主键自增不保证跨方言不复用 id，
    # 残留的旧 ref 也会一直参与检索扫描、让向量库只增不减
    if old_ids:
        await vs.delete_refs(
            KNOWLEDGE_NAMESPACE, [str(chunk_id) for chunk_id in old_ids]
        )
    await vs.upsert_many(
        KNOWLEDGE_NAMESPACE,
        [
            (str(row.id), vec)
            for row, vec in zip(rows, vectors, strict=True)
        ],
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
    external_source_id: str | None = None,
) -> tuple[list[KnowledgeDoc], int]:
    """按筛选条件分页返回文档列表与命中总数。

    limit 为 None 时返回全部（供目录树聚合用）；前端列表必须传 limit，
    否则文档量级增长后前端会一次拉回全量。
    """

    filters = []
    if source_type is not None:
        filters.append(KnowledgeDoc.source_type == source_type)
    if external_source_id is not None:
        filters.append(KnowledgeDoc.external_source_id == external_source_id)
    if path is not None:
        filters.append(KnowledgeDoc.path == path)
    if path_prefix is not None:
        # 本级或子级：与 knowledge_scope.path_condition 同口径，
        # 避免 `网络` 误命中 `网络安全`
        filters.append(path_condition(path_prefix))
    if tag is not None:
        filters.append(_tag_condition(tag))
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
                # PG/SQLite 都有 length()（按字符计）；字节长度方言各异，单独取
                func.length(KnowledgeDoc.content),
                _content_byte_length(),
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


def _tag_condition(tag: str):
    """标签包含过滤：PG 走 JSONB containment；SQLite 走 json_each 展开匹配。"""

    from app.db import engine

    if engine.dialect.name == "sqlite":
        each = func.json_each(KnowledgeDoc.tags).table_valued("value")
        return exists(select(literal(1)).select_from(each).where(each.c.value == tag))
    return KnowledgeDoc.tags.contains([tag])


def _content_byte_length():
    """正文 UTF-8 字节长度表达式：PG 用 octet_length，SQLite 用 BLOB 长度。"""

    from app.db import engine

    if engine.dialect.name == "sqlite":
        return func.length(func.cast(KnowledgeDoc.content, LargeBinary))
    return func.octet_length(KnowledgeDoc.content)


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
    doc_id: str | None = None,
) -> KnowledgeDoc:
    """创建文档；doc_id 由调用方预生成时，可先落盘原件再入库。

    上传流程先存原件后建文档，嵌入失败时调用方负责删除已存原件，
    避免「文档不存在但原件残留」。
    """

    if source_type not in ("manual", "upload", "vault"):
        raise ValueError(f"不支持的 source_type: {source_type}")
    doc = KnowledgeDoc(
        id=doc_id or str(uuid.uuid4()),
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
    if doc.source_type in EXTERNAL_SOURCE_TYPES:
        raise PermissionError("外部源文档随同步刷新，禁止直接修改")
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


async def upsert_external_doc(
    db: AsyncSession,
    *,
    source_id: str,
    source_type: str,
    external_key: str,
    external_hash: str,
    title: str,
    content: str,
    url: str | None,
    path: str,
    enabled: bool = True,
) -> tuple[KnowledgeDoc, bool]:
    """同步写入一篇外部文档；返回 (文档, 是否有变化)。

    正文哈希未变时只更新标题/路径等元信息，不重复切块嵌入；
    这是增量同步的主要成本控制点。enabled 只作用于新建文档，
    既有文档的检索开关由来源启停或用户单独切换维护。
    """

    if source_type not in EXTERNAL_SOURCE_TYPES:
        raise ValueError(f"不支持的外部文档 source_type: {source_type}")
    key = external_key.strip()
    if key == "":
        raise ValueError("external_key 不能为空")
    doc = await db.scalar(
        select(KnowledgeDoc).where(
            KnowledgeDoc.external_source_id == source_id,
            KnowledgeDoc.external_key == key,
        )
    )
    target_title = title.strip() or key.rsplit("/", 1)[-1]
    target_path = _resolve_path(path)
    if doc is None:
        doc = KnowledgeDoc(
            id=str(uuid.uuid4()),
            source_type=source_type,
            title=target_title,
            content=content,
            path=target_path,
            url=url,
            tags=[],
            enabled=enabled,
            external_source_id=source_id,
            external_key=key,
            external_hash=external_hash,
        )
        db.add(doc)
        await db.flush()
        n = await _replace_chunks(db, doc)
        await db.commit()
        await db.refresh(doc)
        logger.info(
            "外部文档首次入库 doc_id=%s source=%s key=%s chunks=%s",
            doc.id,
            source_id,
            key,
            n,
        )
        return doc, True

    if doc.external_hash == external_hash and doc.content == content:
        # 内容未变：只同步可能被来源配置改过的归属目录与标题
        changed = False
        if doc.title != target_title:
            doc.title = target_title
            changed = True
        if doc.path != target_path:
            doc.path = target_path
            changed = True
        if doc.url != url:
            doc.url = url
            changed = True
        if doc.external_hash != external_hash:
            doc.external_hash = external_hash
            changed = True
        if changed:
            doc.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(doc)
        return doc, changed

    doc.source_type = source_type
    doc.title = target_title
    doc.content = content
    doc.path = target_path
    doc.url = url
    doc.external_hash = external_hash
    doc.updated_at = datetime.now(timezone.utc)
    n = await _replace_chunks(db, doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(
        "外部文档更新 doc_id=%s source=%s key=%s chunks=%s",
        doc.id,
        source_id,
        key,
        n,
    )
    return doc, True


async def upsert_vault_doc(
    db: AsyncSession,
    *,
    external_key: str,
    external_hash: str,
    title: str,
    content: str,
    path: str,
) -> tuple[KnowledgeDoc, str]:
    """同步写入一篇内置知识库（vault）文档；返回 (文档, created|updated|unchanged)。

    身份 = external_key（语料文件的绝对路径），external_hash 跳过未变化文件，
    与外部源增量同步同口径。仅同步层调用：应用内 vault 文档只读。
    """

    key = external_key.strip().replace("\\", "/")
    if key == "":
        raise ValueError("external_key 不能为空")
    doc = await db.scalar(
        select(KnowledgeDoc).where(
            KnowledgeDoc.source_type == "vault",
            KnowledgeDoc.external_key == key,
        )
    )
    target_title = title.strip() or key.rsplit("/", 1)[-1]
    target_path = _resolve_path(path)
    if doc is None:
        doc = KnowledgeDoc(
            id=str(uuid.uuid4()),
            source_type="vault",
            title=target_title,
            content=content,
            path=target_path,
            url=None,
            tags=[],
            enabled=True,
            external_key=key,
            external_hash=external_hash,
            source_filename=key.rsplit("/", 1)[-1],
        )
        db.add(doc)
        await db.flush()
        n = await _replace_chunks(db, doc)
        await db.commit()
        await db.refresh(doc)
        logger.info("内置知识文档首次入库 doc_id=%s key=%s chunks=%s", doc.id, key, n)
        return doc, "created"

    if doc.external_hash == external_hash and doc.content == content:
        # 内容未变：只同步可能被文件移动改过的标题与目录
        changed = False
        if doc.title != target_title:
            doc.title = target_title
            changed = True
        if doc.path != target_path:
            doc.path = target_path
            changed = True
        if changed:
            doc.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(doc)
        return doc, "updated" if changed else "unchanged"

    doc.title = target_title
    doc.content = content
    doc.path = target_path
    doc.external_hash = external_hash
    doc.updated_at = datetime.now(timezone.utc)
    n = await _replace_chunks(db, doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("内置知识文档更新 doc_id=%s key=%s chunks=%s", doc.id, key, n)
    return doc, "updated"


async def delete_vault_doc(db: AsyncSession, *, external_key: str) -> bool:
    """同步层按文件删除内置文档（文件消失即删，含切块与向量）。

    仅同步层调用：应用内 vault 文档只读、不可删除。
    """

    key = external_key.strip().replace("\\", "/")
    doc = await db.scalar(
        select(KnowledgeDoc).where(
            KnowledgeDoc.source_type == "vault",
            KnowledgeDoc.external_key == key,
        )
    )
    if doc is None:
        return False
    await _purge_doc(db, doc)
    return True


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
    if doc.source_type in EXTERNAL_SOURCE_TYPES:
        raise PermissionError("外部源文档随同步刷新，禁止移动")
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
    await _purge_doc(db, doc)


async def _purge_doc(db: AsyncSession, doc: KnowledgeDoc) -> None:
    """删文档行并清切块、向量与上传原件；API 层与同步层共用。"""

    # 先记切块 id：业务行删掉后就查不到要清哪些向量了
    chunk_ids: list[int] = list(
        (await db.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.doc_id == doc.id))).all()
    )
    doc_id = doc.id
    source_filename = doc.source_filename
    await db.delete(doc)
    await db.commit()
    vs = get_vector_store()
    if vs is not None and chunk_ids:
        await vs.delete_refs(KNOWLEDGE_NAMESPACE, [str(i) for i in chunk_ids])
    # 原件与文档同生命周期；文件缺失（老数据/手工清理）不阻塞删除
    if source_filename:
        delete_source_file(doc_id=doc_id, filename=source_filename)
    logger.info("删除文档 doc_id=%s chunks=%s", doc_id, len(chunk_ids))


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
            stmt = stmt.where(_tag_condition(t))
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

    from app.models.llm import AppSetting

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
    min_score: float | None = None,
) -> list[KnowledgeHit]:
    """混合检索：向量余弦 + 关键词 ILIKE，RRF 融合后聚合到文档。

    向量通道：先在向量存储里取 top-pool 的 (ref_id, similarity)，
    低于 _VECTOR_MIN_SIMILARITY 的相似度视为噪声丢弃（无关查询应返回空），
    再回业务库 join 出切块与文档并做启停/目录/范围过滤——
    过滤发生在向量截断之后，极端情况下向量通道命中数可能少于 pool。
    """

    query = query.strip()
    if query == "":
        raise ValueError("q 不能为空")
    if limit < 1:
        raise ValueError("limit 必须 >= 1")

    vs = get_vector_store()
    if vs is None:
        raise_vector_store_unavailable()

    pool = limit * 4
    query_vec = await embed_text(query)
    vec_hits = await vs.search(KNOWLEDGE_NAMESPACE, query_vec, pool)
    # (chunk, doc, similarity) 三元组，顺序保持向量相似度降序
    vec_rows: list[tuple[KnowledgeChunk, KnowledgeDoc, float]] = []
    if vec_hits:
        int_ids = [int(ref_id) for ref_id, _ in vec_hits]
        vec_stmt = (
            select(KnowledgeChunk, KnowledgeDoc)
            .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
            .where(KnowledgeChunk.id.in_(int_ids))
            .where(KnowledgeDoc.enabled.is_(True))
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
        row_map: dict[int, tuple[KnowledgeChunk, KnowledgeDoc]] = {
            chunk.id: (chunk, doc) for chunk, doc in (await db.execute(vec_stmt)).all()
        }
        for ref_id, similarity in vec_hits:
            if similarity < _VECTOR_MIN_SIMILARITY:
                continue
            row = row_map.get(int(ref_id))
            if row is not None:
                vec_rows.append((row[0], row[1], similarity))

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
    for rank, (chunk, doc, similarity) in enumerate(vec_rows, start=1):
        docs[doc.id] = doc
        best_chunk.setdefault(doc.id, chunk)
        vector_score.setdefault(doc.id, similarity)
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

    # 阈值可被调用方覆盖（知识库语义检索试调），未传仍走全局设置
    if min_score is None:
        min_score = await resolve_rrf_min_score(db)
    elif not math.isfinite(min_score) or min_score < 0:
        raise ValueError("min_score 必须 >= 0")
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
        import io

        from pypdf import PdfReader

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


def source_file_path(doc_id: str, filename: str) -> Path:
    """上传原件的落盘路径：{存储目录}/{文档 id}{原扩展名}。

    扩展名取自原件名且必须在上传白名单内，防止任意路径拼接。
    """

    lower = filename.lower()
    ext = Path(lower).suffix
    if ext not in (".md", ".txt", ".pdf", ".docx"):
        raise ValueError(f"源文件扩展名不受支持: {ext or '(无)'}")
    directory = Path(get_settings().knowledge_upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{doc_id}{ext}"


def save_source_file(*, doc_id: str, filename: str, raw: bytes) -> None:
    """上传时保存原件字节；写盘失败显式抛错，不静默降级。"""

    path = source_file_path(doc_id, filename)
    path.write_bytes(raw)


def delete_source_file(*, doc_id: str, filename: str) -> None:
    """删除文档时清理原件；文件已不存在视为已清理。"""

    path = source_file_path(doc_id, filename)
    path.unlink(missing_ok=True)


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
