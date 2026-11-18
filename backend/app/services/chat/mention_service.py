"""@ 提及检索：按关键词查可引用的文档、MCP Server、技能、工作流、助理、会话。"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import UserAgent
from app.models.chat import ChatSession
from app.models.knowledge import KnowledgeDoc
from app.models.mcp import McpServer
from app.models.skill import UserSkill
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

# 提及类型：与前端 chat.ts 的 MentionType 对齐
MENTION_TYPES: frozenset[str] = frozenset(
    {"file", "server", "skill", "workflow", "agent", "session"}
)
MAX_MENTION_RESULTS = 50


async def search_mentions(
    db: AsyncSession,
    *,
    q: str,
    mention_type: str | None,
    limit: int,
    owner_user_id: int | None = None,
    is_admin: bool = False,
) -> list[dict]:
    """按关键词检索可提及对象；返回前端 MentionItem 形状。

    session 类型按 owner_user_id 过滤（admin 放行），防止跨用户泄露会话标题。
    """

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

    if mention_type is None or mention_type == "skill":
        skill_stmt = select(UserSkill).order_by(UserSkill.key.asc()).limit(limit)
        if q != "":
            skill_stmt = skill_stmt.where(
                or_(UserSkill.key.ilike(f"%{q}%"), UserSkill.description.ilike(f"%{q}%"))
            )
        for skill in (await db.scalars(skill_stmt)).all():
            items.append(
                {
                    "type": "skill",
                    "id": skill.key,
                    "label": skill.key,
                    "subtitle": skill.description[:100],
                }
            )

    if mention_type is None or mention_type == "workflow":
        wf_stmt = select(Workflow).order_by(Workflow.name.asc()).limit(limit)
        if q != "":
            wf_stmt = wf_stmt.where(Workflow.name.ilike(f"%{q}%"))
        for wf in (await db.scalars(wf_stmt)).all():
            items.append(
                {
                    "type": "workflow",
                    "id": str(wf.id),
                    "label": wf.name,
                    "subtitle": (wf.description or "")[:100],
                }
            )

    if mention_type is None or mention_type == "agent":
        agent_stmt = select(UserAgent).order_by(UserAgent.name.asc()).limit(limit)
        if q != "":
            agent_stmt = agent_stmt.where(
                or_(
                    UserAgent.name.ilike(f"%{q}%"),
                    UserAgent.display_name.ilike(f"%{q}%"),
                )
            )
        for agent in (await db.scalars(agent_stmt)).all():
            items.append(
                {
                    "type": "agent",
                    "id": agent.name,
                    "label": agent.display_name or agent.name,
                    "subtitle": (agent.description or "")[:100],
                }
            )

    if mention_type is None or mention_type == "session":
        sess_stmt = select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(limit)
        if q != "":
            sess_stmt = sess_stmt.where(ChatSession.title.ilike(f"%{q}%"))
        if not is_admin and owner_user_id is not None:
            sess_stmt = sess_stmt.where(
                or_(
                    ChatSession.owner_user_id == owner_user_id,
                    (ChatSession.owner_user_id.is_(None) & (ChatSession.adk_user_id == str(owner_user_id))),
                )
            )
        for sess in (await db.scalars(sess_stmt)).all():
            items.append(
                {
                    "type": "session",
                    "id": sess.id,
                    "label": sess.title,
                    "subtitle": sess.id[:12],
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
