"""跨会话记忆：真实嵌入写入 + 语义回忆 + 作用域过滤 + 删除清理。"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.services import memory_service


async def test_memory_remember_and_recall(monkeypatch, tmp_path) -> None:
    """真实嵌入写入后，同义 query 能召回；作用域过滤生效；删除后不再命中。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        async with SessionLocal() as db:
            await memory_service.remember(
                db,
                content="用户偏好：回复时使用简体中文并给出操作步骤。",
                agent_id="ops-helper",
            )
            await memory_service.remember(
                db,
                content="生产环境的告警阈值由网管系统统一配置。",
                agent_id=None,  # 全局记忆
            )
            await db.commit()

        async with SessionLocal() as db:
            hits = await memory_service.recall(
                db, query="用户喜欢什么样的回复格式？", agent_id="ops-helper"
            )
            assert len(hits) >= 1
            assert any("简体中文" in h["content"] for h in hits)
            # 召回包含本助理记忆与全局记忆，不包含其他助理的
            assert all(h["agent_id"] in (None, "ops-helper") for h in hits)

        # 作用域过滤：别的助理回忆不到 ops-helper 的私有记忆
        async with SessionLocal() as db:
            hits = await memory_service.recall(
                db, query="用户喜欢什么样的回复格式？", agent_id="other-agent"
            )
            assert all(h["agent_id"] is None for h in hits)
    finally:
        get_settings.cache_clear()


async def test_memory_reject_empty_and_too_long() -> None:
    """空内容 / 超长内容显式失败。"""

    with pytest.raises(ValueError, match="不能为空"):
        await memory_service.remember(None, content="   ")
    with pytest.raises(ValueError, match="过长"):
        await memory_service.remember(None, content="x" * 2001)


async def test_memory_forget_removes_vector(monkeypatch, tmp_path) -> None:
    """删除记忆后同一 query 不再召回。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        async with SessionLocal() as db:
            item = await memory_service.remember(
                db,
                content="备用链路切换命令是 display standby-link switch。",
                agent_id=None,
            )
            await db.commit()

        async with SessionLocal() as db:
            hits = await memory_service.recall(db, query="备用链路切换", agent_id=None)
            assert any(h["id"] == item.id for h in hits)

        async with SessionLocal() as db:
            await memory_service.forget(db, item.id)
            await db.commit()

        async with SessionLocal() as db:
            hits = await memory_service.recall(db, query="备用链路切换", agent_id=None)
            assert all(h["id"] != item.id for h in hits)
    finally:
        get_settings.cache_clear()
