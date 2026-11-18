"""审批创建通知：新建待办要实时推给会话所属用户，去重早退不重复推。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_db_engine
from app.models.base import Base
from app.models.chat import ChatSession
from app.redis import get_redis
from app.services.chat import approval_service
from app.services.platform import notification_bus


def _new_sessionmaker(path):
    """每个用例独立建库建表：避免跨事件循环复用连接，也不污染开发库。"""

    engine = create_db_engine(f"sqlite+aiosqlite:///{path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_create_pending_approval_publishes_notification(tmp_path) -> None:
    """新建审批单后：该用户 notify 流收到 approval_created（真 Redis，不 mock）。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "notify.db")
    owner = 910001
    session_id = f"s-appr-notify-{uuid.uuid4().hex[:8]}"
    created_ids: list[str] = []
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sessionmaker() as db:
            db.add(
                ChatSession(
                    id=session_id,
                    title="通知测试会话",
                    provider="p",
                    model="m",
                    agent_id="ops",
                    adk_user_id="u",
                    owner_user_id=owner,
                )
            )
            await db.commit()

        redis = get_redis()
        stream_key = notification_bus._key(str(owner))
        # 隔离本用例：清掉该测试 uid 的历史帧，之后 xrange 看到的都是本次新增。
        await redis.delete(stream_key)

        async with sessionmaker() as db:
            row = await approval_service.create_pending_approval(
                db,
                session_id=session_id,
                confirmation_fc_id=f"fc-{uuid.uuid4().hex[:8]}",
                tool_call_id="tc-notify",
                tool_name="run_python",
                hint="echo hi",
                arguments={},
                trace_id="trace-notify",
            )
            created_ids.append(row.id)

        added = await redis.xrange(stream_key, min="-", max="+")
        created = [
            (sid, fields)
            for sid, fields in added
            if fields.get("kind") == "approval_created"
        ]
        assert len(created) == 1
        sid, fields = created[0]
        assert row.id in fields.get("payload", "")
        assert session_id in fields.get("payload", "")

        # 去重早退：同 confirmation_fc_id 再调一次不应新增审批，也不应再发通知。
        async with sessionmaker() as db:
            same = await approval_service.create_pending_approval(
                db,
                session_id=session_id,
                confirmation_fc_id=row.confirmation_fc_id,
                tool_call_id="tc-notify",
                tool_name="run_python",
                hint="echo hi",
                arguments={},
                trace_id="trace-notify",
            )
            assert same.id == row.id

        added2 = await redis.xrange(stream_key, min="-", max="+")
        created2 = [
            (sid, fields)
            for sid, fields in added2
            if fields.get("kind") == "approval_created"
        ]
        assert len(created2) == 1

        # 清理测试通知帧，不污染长期 notify 流；业务行随临时库销毁。
        await redis.delete(stream_key)
    finally:
        await engine.dispose()
