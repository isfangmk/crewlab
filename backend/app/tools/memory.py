"""跨会话记忆工具：模型可在对话中主动保存与回忆长期记忆。

作用域口径：工具在助理会话中调用时按「本助理记忆 ∪ 全局记忆」召回；
保存时归属当前助理。全局记忆对全部助理可见。
"""

from __future__ import annotations

from typing import Any

from app.db import SessionLocal
from app.services.memory_service import recall, remember


async def remember_memory(content: str, tool_context: Any = None) -> dict:
    """把一条值得长期记住的事实/偏好保存到跨会话记忆。

    content: 要记住的内容，一句话描述一个事实或偏好（不要批量罗列）。
    """

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    # 助理 id 与归属人从会话行取：stream_session_message 的会话已绑定 agent_id
    agent_id = None
    created_by = None
    if session_id:
        from app.models.chat import ChatSession

        async with SessionLocal() as db:
            row = await db.get(ChatSession, session_id)
            if row is not None:
                agent_id = row.agent_id
                created_by = row.owner_user_id

    async with SessionLocal() as db:
        item = await remember(
            db,
            content=content,
            agent_id=agent_id,
            session_id=session_id or None,
            created_by=created_by,
        )
        return {
            "status": "saved",
            "id": item.id,
            "scope": agent_id or "global",
            "content": item.content,
        }


async def recall_memory(query: str, tool_context: Any = None) -> dict:
    """按语义回忆相关记忆（本助理记忆 ∪ 全局记忆），按相关度排序返回。

    query: 想回忆的内容描述，如「用户偏好的回复语言」。
    """

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    agent_id = None
    if session_id:
        from app.models.chat import ChatSession

        async with SessionLocal() as db:
            row = await db.get(ChatSession, session_id)
            agent_id = row.agent_id if row else None

    async with SessionLocal() as db:
        items = await recall(db, query=query, agent_id=agent_id)
        return {
            "items": items,
            "total": len(items),
        }
