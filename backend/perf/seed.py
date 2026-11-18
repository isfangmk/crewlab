"""造 __perf__ 专属压测数据并产出 manifest（参数名→真实 id 池）。

用法：
  .venv/Scripts/python.exe -m perf.seed            # 造数据
  .venv/Scripts/python.exe -m perf.seed --clean    # 清理所有 __perf__ 数据

数据严格用 __perf__ 前缀标记 title/content，清理按此前缀精确定位，不碰用户数据。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models.chat import ChatMessage, ChatSession
from app.models.memory import MemoryItem
from app.services.platform.llm_config import get_default_selection
from perf.env import load_manifest, save_manifest

TAG = "__perf__"
TAG_SESSION_COUNT = 300          # 会话列表压测量
BIG_SESSION_COUNT = 5            # 大消息量会话数
BIG_SESSION_MSG_COUNT = 1000     # 每个大会话的消息数（测消息列表索引）
MEMORY_COUNT = 20
QUEUE_ITEM_COUNT = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def seed() -> None:
    async with SessionLocal() as db:
        # 幂等：先清旧数据
        await clean_db(db)

        # 用系统默认 LLM（列表/新建会话的同一选择），不写死 provider/model，
        # 否则压测数据与真实环境模型不一致，模型筛选/统计等路径测不到真实值。
        provider, model = await get_default_selection(db)

        sessions: list[ChatSession] = []
        big_ids: list[str] = []
        for i in range(TAG_SESSION_COUNT):
            sid = f"perf-{uuid.uuid4().hex}"
            sessions.append(
                ChatSession(
                    id=sid,
                    title=f"{TAG} 会话 {i}",
                    provider=provider,
                    model=model,
                    agent_id="default",
                    adk_user_id="1",
                    owner_user_id=1,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            if i < BIG_SESSION_COUNT:
                big_ids.append(sid)
        db.add_all(sessions)
        await db.commit()
        print(f"已建 {len(sessions)} 个 {TAG} 会话")

        # 大会话消息：session_id + seq 正是缺失的复合索引列
        messages: list[ChatMessage] = []
        for sid in big_ids:
            for seq in range(BIG_SESSION_MSG_COUNT):
                role = "user" if seq % 2 == 0 else "assistant"
                messages.append(
                    ChatMessage(
                        id=f"perfmsg-{uuid.uuid4().hex}",
                        session_id=sid,
                        role=role,
                        kind="message",
                        content=f"{TAG} 消息 {seq} " + "正文填充" * 20,
                        seq=seq,
                        created_at=_now(),
                    )
                )
        # 分批提交，避免单事务过大
        for start in range(0, len(messages), 2000):
            db.add_all(messages[start : start + 2000])
            await db.commit()
        print(f"已建 {len(messages)} 条 {TAG} 消息（{BIG_SESSION_COUNT} 个大会话）")

        # 少量记忆（tag 隔离）
        memories: list[MemoryItem] = []
        for i in range(MEMORY_COUNT):
            memories.append(
                MemoryItem(
                    id=f"perfmem-{uuid.uuid4().hex}",
                    content=f"{TAG} memory content {i}",
                    scope_agent_id=None,
                    enabled=True,
                    created_by=1,
                    created_at=_now(),
                )
            )
        db.add_all(memories)
        await db.commit()
        print(f"已建 {len(memories)} 条 {TAG} 记忆")

        manifest: dict[str, Any] = {
            "tag": TAG,
            "session_ids": [s.id for s in sessions],
            "big_session_ids": big_ids,
            "memory_ids": [m.id for m in memories],
            "provider": provider,
            "model": model,
            "admin_uid": 1,
        }
        save_manifest(manifest)
        print(f"已写 perf/manifest.json（{len(sessions)} 会话 / {len(memories)} 记忆）")


async def clean_db(db: Any) -> None:
    """按 tag 精确删除 __perf__ 数据，不碰用户数据。"""

    sid_rows = (
        await db.execute(select(ChatSession.id).where(ChatSession.title.like(f"{TAG}%")))
    ).scalars().all()
    sids = list(sid_rows)
    if sids:
        await db.execute(
            ChatMessage.__table__.delete().where(ChatMessage.session_id.in_(sids))
        )
        await db.execute(
            ChatSession.__table__.delete().where(ChatSession.id.in_(sids))
        )
    await db.execute(
        MemoryItem.__table__.delete().where(MemoryItem.content.like(f"{TAG}%"))
    )
    await db.commit()
    if sids:
        print(f"清理旧 {TAG} 数据：{len(sids)} 会话")


async def clean() -> None:
    async with SessionLocal() as db:
        await clean_db(db)
    # 清 Redis 队列 key（专属会话）
    try:
        manifest = load_manifest()
    except RuntimeError:
        manifest = None  # 无 manifest：本次没有可清理的专属会话 key
    if manifest is not None:
        try:
            import redis

            from app.redis import close, connect, get_redis
            from app.services.chat import chat_queue as q

            await connect()
            r = get_redis()
            for sid in manifest.get("session_ids", []):
                await r.delete(q._order_key(sid), q._data_key(sid), q._int_key(sid))
            await close()
            print("已清理 Redis 队列 key")
        except redis.RedisError:
            # Redis 未起：DB 数据已清理，队列 key 会随 TTL 自然过期
            print("Redis 不可用，跳过队列 key 清理")
    print("清理完成")


if __name__ == "__main__":
    if "--clean" in sys.argv:
        asyncio.run(clean())
    else:
        asyncio.run(seed())
