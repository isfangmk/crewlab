"""跨会话长期记忆：存储、语义回忆与生命周期管理。

向量写入与检索复用 app.vector_store（MEMORY_NAMESPACE）；
`VECTOR_BACKEND=none` 时记忆功能显式不可用（依赖语义检索，不做静默降级）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import MemoryItem
from app.services.agent import agent_service
from app.vector_store import MEMORY_NAMESPACE, get_vector_store, raise_vector_store_unavailable
from app.services.platform.embedding_client import embed_text

logger = logging.getLogger(__name__)

# 回忆时单次召回的条数上限
RECALL_LIMIT = 5
# 单条记忆内容长度上限
MEMORY_CONTENT_LIMIT = 2000
# 管理列表单页上限；记录会持续增长，不能一次拉全表
MAX_LIST_LIMIT = 200


def _normalize_scope(agent_id: str | None) -> str | None:
    if agent_id is None:
        return None
    scope = agent_id.strip()
    return scope or None


async def _validate_scope(db: AsyncSession, agent_id: str | None) -> str | None:
    """作用域必须是已存在的助理；空作用域表示全局记忆。"""

    scope = _normalize_scope(agent_id)
    if scope is None:
        return None
    try:
        await agent_service.get_agent(db, scope)
    except LookupError as exc:
        raise ValueError(f"作用域助理不存在: {scope}") from exc
    return scope


async def remember(
    db: AsyncSession,
    *,
    content: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    created_by: int | None = None,
) -> MemoryItem:
    """写一条记忆：业务行 + 向量（语义回忆用）。

    先算向量、再 flush 业务行、最后 upsert 向量并提交：
    任何一步失败都回滚业务行，避免出现「列表里有、但永远检索不到」的记忆。
    """

    if not isinstance(content, str) or content.strip() == "":
        raise ValueError("记忆内容不能为空")
    if len(content) > MEMORY_CONTENT_LIMIT:
        raise ValueError(f"记忆内容过长（上限 {MEMORY_CONTENT_LIMIT} 字符）")
    scope = await _validate_scope(db, agent_id)
    vs = get_vector_store()
    if vs is None:
        raise_vector_store_unavailable()

    text = content.strip()
    vector = await embed_text(text)
    item = MemoryItem(
        id=str(uuid.uuid4()),
        content=text,
        scope_agent_id=scope,
        source_session_id=session_id or None,
        created_by=created_by,
    )
    db.add(item)
    await db.flush()
    try:
        await vs.upsert_many(MEMORY_NAMESPACE, [(str(item.id), vector)])
        await db.commit()
    except Exception:
        await db.rollback()
        # 向量可能已经写入；尽力清掉，失败只留日志（业务行已回滚）
        try:
            await vs.delete_refs(MEMORY_NAMESPACE, [item.id])
        except Exception:  # noqa: BLE001 — 清理失败不掩盖原始异常
            logger.exception("记忆向量回滚失败 id=%s", item.id)
        raise
    await db.refresh(item)
    logger.info(
        "已保存记忆 id=%s agent=%s len=%s", item.id, scope, len(item.content)
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
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise ValueError(f"limit 必须在 1..{MAX_LIST_LIMIT} 之间")
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
            "source_session_id": row.source_session_id,
            "similarity": similarity,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for _, similarity, row in scored
    ]


async def forget(db: AsyncSession, memory_id: str) -> None:
    """删除一条记忆（业务行 + 向量）。

    先删业务行：即使向量删除失败，回忆侧 join 不到行，也不会返回已删除内容；
    残留向量只是孤儿，不影响语义。
    """

    row = await db.get(MemoryItem, memory_id)
    if row is None:
        raise LookupError(f"记忆不存在: {memory_id}")
    await db.delete(row)
    await db.commit()
    vs = get_vector_store()
    if vs is not None:
        try:
            await vs.delete_refs(MEMORY_NAMESPACE, [memory_id])
        except Exception:  # noqa: BLE001 — 业务行已删，向量清理失败只留日志
            logger.exception("记忆向量删除失败 id=%s", memory_id)
    logger.info("已删除记忆 id=%s", memory_id)


async def set_enabled(db: AsyncSession, memory_id: str, enabled: bool) -> MemoryItem:
    """启用/停用记忆；停用后不再参与语义回忆，向量保留以便重新启用。"""

    row = await db.get(MemoryItem, memory_id)
    if row is None:
        raise LookupError(f"记忆不存在: {memory_id}")
    row.enabled = enabled
    await db.commit()
    await db.refresh(row)
    logger.info("更新记忆启停 id=%s enabled=%s", memory_id, enabled)
    return row


async def list_memories(
    db: AsyncSession,
    *,
    q: str | None = None,
    agent_id: str | None = None,
    enabled: bool | None = None,
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[MemoryItem], int]:
    """按作用域/关键字/启停状态列记忆（管理页用），返回当前页与筛选后总数。"""

    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise ValueError(f"limit 必须在 1..{MAX_LIST_LIMIT} 之间")
    if offset < 0:
        raise ValueError("offset 不能为负")
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir 非法: {sort_dir}")
    conditions = []
    scope = _normalize_scope(agent_id)
    if scope is not None:
        conditions.append(MemoryItem.scope_agent_id == scope)
    if enabled is not None:
        conditions.append(MemoryItem.enabled.is_(enabled))
    if q is not None and q.strip() != "":
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(
                MemoryItem.content.ilike(needle),
                MemoryItem.scope_agent_id.ilike(needle),
                MemoryItem.source_session_id.ilike(needle),
            )
        )
    stmt = select(MemoryItem)
    count_stmt = select(func.count()).select_from(MemoryItem)
    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)
    ordered = MemoryItem.created_at.asc() if sort_dir == "asc" else MemoryItem.created_at.desc()
    tie_break = MemoryItem.id.asc() if sort_dir == "asc" else MemoryItem.id.desc()
    stmt = stmt.order_by(ordered, tie_break).limit(limit).offset(offset)
    rows = list((await db.scalars(stmt)).all())
    total = int(await db.scalar(count_stmt) or 0)
    return rows, total
