"""跨会话记忆工具：模型可在对话中主动保存与回忆长期记忆。

作用域口径：工具在助理会话中调用时按「本助理记忆 ∪ 全局记忆」召回；
保存时归属当前助理。全局记忆对全部助理可见。
"""

from __future__ import annotations

from typing import Any

from app.db import SessionLocal
from app.models.memory import MemoryItem
from app.services.knowledge.memory_service import forget, recall, remember, update_content


async def _agent_id_from_context(tool_context: Any) -> str | None:
    """从工具上下文取当前助理 id；手动运行入口无上下文时返回 None（全局）。"""

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    if not session_id:
        return None
    from app.models.chat import ChatSession

    async with SessionLocal() as db:
        row = await db.get(ChatSession, session_id)
        return row.agent_id if row is not None else None


async def _assert_memory_in_scope(db, memory_id: str, agent_id: str | None) -> MemoryItem:
    """改/删前校验归属：本助理记忆 ∪ 全局记忆才允许，防跨助理改删。"""

    row = await db.get(MemoryItem, memory_id)
    if row is None:
        raise LookupError(f"记忆不存在: {memory_id}")
    if row.scope_agent_id is not None and row.scope_agent_id != agent_id:
        raise LookupError(f"记忆不属于当前助理: {memory_id}")
    return row


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


async def update_memory(memory_id: str, content: str, tool_context: Any = None) -> dict:
    """改写一条已有记忆的正文；先用 recall_memory 拿到 memory_id 再改。

    memory_id: 要修改的记忆 id，来自 recall_memory 返回的 items[].id。
    content: 修改后的完整正文，一句话描述一个事实或偏好（会整体替换旧正文）。
    """

    agent_id = await _agent_id_from_context(tool_context)
    async with SessionLocal() as db:
        await _assert_memory_in_scope(db, memory_id, agent_id)
        item = await update_content(db, memory_id, content)
        return {
            "status": "updated",
            "id": item.id,
            "content": item.content,
        }


async def delete_memory(memory_id: str, tool_context: Any = None) -> dict:
    """删除一条记忆；先用 recall_memory 拿到 memory_id 再删，不可恢复。

    memory_id: 要删除的记忆 id，来自 recall_memory 返回的 items[].id。
    """

    agent_id = await _agent_id_from_context(tool_context)
    async with SessionLocal() as db:
        await _assert_memory_in_scope(db, memory_id, agent_id)
        await forget(db, memory_id)
        return {
            "status": "deleted",
            "id": memory_id,
        }
