"""@ 提及检索：按关键词查可引用的知识库文档与 MCP Server。"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.knowledge import KnowledgeDoc
from app.models_orm.mcp import McpServer

logger = logging.getLogger(__name__)

# 提及类型：与前端 chat.ts 的 MentionType 对齐
MENTION_TYPES: frozenset[str] = frozenset({"file", "server"})
MAX_MENTION_RESULTS = 50


async def search_mentions(
    db: AsyncSession,
    *,
    q: str,
    mention_type: str | None,
    limit: int,
) -> list[dict]:
    """按关键词检索可提及对象；返回前端 MentionItem 形状。"""

    if mention_type is not None and mention_type not in MENTION_TYPES:
        raise ValueError(f"不支持的 type: {mention_type}")
    if limit < 1 or limit > MAX_MENTION_RESULTS:
        raise ValueError(f"limit 范围是 1..{MAX_MENTION_RESULTS}")

    items: list[dict] = []

    if mention_type is None or mention_type == "file":
        doc_stmt = select(KnowledgeDoc).order_by(KnowledgeDoc.updated_at.desc()).limit(limit)
        if q != "":
            doc_stmt = doc_stmt.where(
                or_(KnowledgeDoc.title.ilike(f"%{q}%"), KnowledgeDoc.path.ilike(f"%{q}%"))
            )
        for doc in (await db.scalars(doc_stmt)).all():
            items.append(
                {
                    "type": "file",
                    "id": doc.id,
                    "label": doc.title,
                    "subtitle": doc.path,
                }
            )

    if mention_type is None or mention_type == "server":
        srv_stmt = select(McpServer).order_by(McpServer.id.asc()).limit(limit)
        if q != "":
            srv_stmt = srv_stmt.where(McpServer.name.ilike(f"%{q}%"))
        for server in (await db.scalars(srv_stmt)).all():
            items.append(
                {
                    "type": "server",
                    "id": server.name,
                    "label": server.name,
                    "subtitle": server.endpoint,
                }
            )

    items = items[:limit]
    logger.info(
        "提及检索 q_len=%s type=%s limit=%s hits=%s",
        len(q),
        mention_type,
        limit,
        len(items),
    )
    return items
