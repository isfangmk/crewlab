"""消息正文搜索：ilike 转义、按会话聚合、跨用户隔离（真库，专属 tag）。"""

from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal
from app.models.chat import ChatMessage, ChatSession
from app.services.chat.chat_service import search_messages

TAG = f"msg-search-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_search_messages_scoped_and_aggregated() -> None:
    """关键词命中、%/_ 作为字面量转义、一条会话只出一个片段、跨用户隔离。"""

    owner = 920001
    other = 920002
    sid = f"s-{TAG}"
    sid_other = f"s-other-{TAG}"
    mids: list[str] = []
    try:
        async with SessionLocal() as db:
            db.add(
                ChatSession(id=sid, title="搜索测试会话", provider="p", model="m", adk_user_id=str(owner), owner_user_id=owner)
            )
            db.add(
                ChatSession(id=sid_other, title="他人会话", provider="p", model="m", adk_user_id=str(other), owner_user_id=other)
            )
            await db.flush()
            for idx, content in enumerate(
                [
                    f"{TAG} 第一条包含 100% 的正文",
                    f"{TAG} 第二条包含 a_b 下划线",
                    f"{TAG} 第三条同会话命中",
                ]
            ):
                mid = f"m-{TAG}-{idx}"
                db.add(
                    ChatMessage(id=mid, session_id=sid, role="assistant", kind="message", content=content, seq=idx + 1)
                )
                mids.append(mid)
            other_mid = f"m-other-{TAG}"
            db.add(
                ChatMessage(id=other_mid, session_id=sid_other, role="assistant", kind="message", content=f"{TAG} 他人私密内容", seq=1)
            )
            mids.append(other_mid)
            await db.commit()

        async with SessionLocal() as db:
            # 普通关键词：命中本会话（按会话聚合只出 1 条），看不到他人会话。
            hits = await search_messages(db, q=TAG, limit=10, owner_user_id=owner)
            assert len(hits) == 1
            assert hits[0]["session_id"] == sid
            assert hits[0]["session_title"] == "搜索测试会话"
            assert hits[0]["message_id"] in mids
            assert hits[0]["role"] == "assistant"

            # 通配符按字面量转义：查 100% 只命中含 100% 的那条，不因 % 匹配全部。
            pct = await search_messages(db, q="100%", limit=10, owner_user_id=owner)
            assert len(pct) == 1
            assert "100%" in pct[0]["snippet"]

            under = await search_messages(db, q="a_b", limit=10, owner_user_id=owner)
            assert len(under) == 1
            assert "a_b" in under[0]["snippet"]

            # 他人身份只看到自己的会话。
            other_hits = await search_messages(db, q=TAG, limit=10, owner_user_id=other)
            assert len(other_hits) == 1
            assert other_hits[0]["session_id"] == sid_other

            # admin 放行：能看到两个会话的命中。
            admin_hits = await search_messages(db, q=TAG, limit=10, owner_user_id=owner, is_admin=True)
            assert {h["session_id"] for h in admin_hits} == {sid, sid_other}

            with pytest.raises(ValueError):
                await search_messages(db, q="  ", limit=10, owner_user_id=owner)
    finally:
        async with SessionLocal() as db:
            for mid in mids:
                msg = await db.get(ChatMessage, mid)
                if msg is not None:
                    await db.delete(msg)
            for s in (sid, sid_other):
                session = await db.get(ChatSession, s)
                if session is not None:
                    await db.delete(session)
            await db.commit()
