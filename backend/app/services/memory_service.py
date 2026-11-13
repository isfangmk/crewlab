"""跨会话长期记忆：存储、语义回忆与生命周期管理。

向量写入与检索复用 app.vector_store（MEMORY_NAMESPACE）；
`VECTOR_BACKEND=none` 时记忆功能显式不可用（依赖语义检索，不做静默降级）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.memory import MemoryItem
from app.vector_store import MEMORY_NAMESPACE, get_vector_store, raise_vector_store_unavailable
from app.services.embedding_client import embed_text

logger = logging.getLogger(__name__)

# 回忆时单次召回的条数上限
RECALL_LIMIT = 5
# 单条记忆内容长度上限
MEMORY_CONTENT_LIMIT = 2000


async def remember(
    db: AsyncSession,
    *,
    content: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    created_by: int | None = None,
) -> MemoryItem:
    """写一条记忆：业务行 + 向量（语义回忆用）。"""

    if not isinstance(content, str) or content.strip() == "":
        raise ValueError("记忆内容不能为空")
    if len(content) > MEMORY_CONTENT_LIMIT:
        raise ValueError(f"记忆内容过长（上限 {MEMORY_CONTENT_LIMIT} 字符）")
    vs = get_vector_store()
    if vs is None:
        raise_vector_store_unavailable()

    item = MemoryItem(
        id=str(uuid.uuid4()),
        content=content.strip(),
        scope_agent_id=agent_id or None,
        source_session_id=session_id or None,
        created_by=created_by,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await vs.upsert_many(
        MEMORY_NAMESPACE,
        [(str(item.id), await embed_text(item.content))],
    )
    logger.info(
        "已保存记忆 id=%s agent=%s len=%s", item.id, agent_id, len(item.content)
    )
    return item


async def recall(
    db: AsyncSession,
    *,
    query: str,
    agent_id: str | None = None,
    limit: int = RECALL_LIMIT,
) -> list[dict[str, Any]]:
    """语义回忆：向量召回 + 回业务库 join（enabled 过滤 + 作用域过滤）。

    作用域口径：本助理的记忆 ∪ 全局记忆。
    """

    if not isinstance(query, str) or query.strip() == "":
        raise ValueError("query 不能为空")
    vs = get_vector_store()
    if vs is None:
        raise_vector_store_unavailable()

    hits = await vs.search(MEMORY_NAMESPACE, await embed_text(query.strip()), limit * 4)
    if not hits:
        return []
    ref_ids = [ref for ref, _ in hits]
    rows = await db.scalars(
        select(MemoryItem).where(MemoryItem.id.in_(ref_ids), MemoryItem.enabled.is_(True))
    )
    by_id = {row.id: row for row in rows.all()}
    scored = [
        (ref, similarity, by_id[ref])
        for ref, similarity in hits
        if ref in by_id
        and (by_id[ref].scope_agent_id is None or by_id[ref].scope_agent_id == agent_id)
    ][:limit]
    return [
        {
            "id": row.id,
            "content": row.content,
            "agent_id": row.scope_agent_id,
            "similarity": similarity,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for _, similarity, row in scored
    ]


async def forget(db: AsyncSession, memory_id: str) -> None:
    """删除一条记忆（业务行 + 向量）；向量存储未启用时只删业务行。"""

    row = await db.get(MemoryItem, memory_id)
    if row is None:
        raise LookupError(f"记忆不存在: {memory_id}")
    await db.delete(row)
    await db.commit()
    vs = get_vector_store()
    if vs is not None:
        await vs.delete_refs(MEMORY_NAMESPACE, [memory_id])
    logger.info("已删除记忆 id=%s", memory_id)


async def list_memories(
    db: AsyncSession, *, agent_id: str | None = None, limit: int = 100
) -> list[MemoryItem]:
    """按作用域列记忆（管理页用）；agent_id 为 None 列全部。"""

    stmt = select(MemoryItem).order_by(MemoryItem.created_at.desc()).limit(limit)
    if agent_id is not None:
        stmt = stmt.where(MemoryItem.scope_agent_id == agent_id)
    rows = await db.scalars(stmt)
    return list(rows.all())
