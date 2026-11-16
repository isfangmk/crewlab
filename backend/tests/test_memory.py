"""跨会话记忆：真实嵌入写入 + 语义回忆 + 作用域过滤 + 管理查询。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models.memory import MemoryItem
from app.services.knowledge import memory_service
from tests.conftest import login_access_token


async def _cleanup(ids: list[str]) -> None:
    """测试数据必须自行清理（业务行 + 向量），避免污染本机长期记忆。"""

    if not ids:
        return
    async with SessionLocal() as db:
        for memory_id in ids:
            try:
                await memory_service.forget(db, memory_id)
            except LookupError:
                continue


async def test_memory_remember_and_recall(monkeypatch, tmp_path) -> None:
    """真实嵌入写入后，同义 query 能召回；作用域过滤生效；删除后不再命中。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    created: list[str] = []
    try:
        async with SessionLocal() as db:
            item = await memory_service.remember(
                db,
                content="用户偏好：回复时使用简体中文并给出操作步骤。",
                agent_id="default",
            )
            created.append(item.id)
            item = await memory_service.remember(
                db,
                content="生产环境的告警阈值由网管系统统一配置。",
                agent_id=None,  # 全局记忆
            )
            created.append(item.id)

        async with SessionLocal() as db:
            hits = await memory_service.recall(
                db, query="用户喜欢什么样的回复格式？", agent_id="default"
            )
            assert len(hits) >= 1
            assert any("简体中文" in h["content"] for h in hits)
            # 召回包含本助理记忆与全局记忆，不包含其他助理的
            assert all(h["agent_id"] in (None, "default") for h in hits)

        # 作用域过滤：别的助理回忆不到 default 的私有记忆
        async with SessionLocal() as db:
            hits = await memory_service.recall(
                db, query="用户喜欢什么样的回复格式？", agent_id="other-agent"
            )
            assert all(h["agent_id"] is None for h in hits)
    finally:
        await _cleanup(created)
        get_settings.cache_clear()


async def test_memory_reject_empty_and_too_long() -> None:
    """空内容 / 超长内容显式失败；未知作用域助理显式失败。"""

    with pytest.raises(ValueError, match="不能为空"):
        # 参数校验在触达数据库之前完成，这里传 None 是刻意验证短路
        await memory_service.remember(None, content="   ")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="过长"):
        await memory_service.remember(None, content="x" * 2001)  # pyright: ignore[reportArgumentType]
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="作用域助理不存在"):
            await memory_service.remember(
                db, content="测试作用域", agent_id="no-such-agent-memory"
            )


async def test_memory_forget_removes_vector(monkeypatch, tmp_path) -> None:
    """删除记忆后同一 query 不再召回。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    item_id = ""
    try:
        async with SessionLocal() as db:
            item = await memory_service.remember(
                db,
                content="备用链路切换命令是 display standby-link switch。",
                agent_id=None,
            )
            item_id = item.id

        async with SessionLocal() as db:
            hits = await memory_service.recall(db, query="备用链路切换", agent_id=None)
            assert any(h["id"] == item_id for h in hits)

        async with SessionLocal() as db:
            await memory_service.forget(db, item_id)
            deleted_id = item_id
            item_id = ""

        async with SessionLocal() as db:
            hits = await memory_service.recall(db, query="备用链路切换", agent_id=None)
            assert all(h["id"] != deleted_id for h in hits)
    finally:
        if item_id:
            await _cleanup([item_id])
        get_settings.cache_clear()


async def test_memory_list_search_pagination_and_toggle(monkeypatch, tmp_path) -> None:
    """管理列表：关键字命中、服务端分页、启停状态可切换。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    tag = f"mem-list-{uuid.uuid4().hex[:8]}"
    created: list[str] = []
    try:
        async with SessionLocal() as db:
            for index in range(3):
                item = await memory_service.remember(
                    db,
                    content=f"{tag} 第 {index} 条：偏好设置 {index}",
                    agent_id=None,
                )
                created.append(item.id)

        async with SessionLocal() as db:
            rows, total = await memory_service.list_memories(db, q=tag, limit=2, offset=0)
            assert total == 3
            assert len(rows) == 2
            rows2, total2 = await memory_service.list_memories(db, q=tag, limit=2, offset=2)
            assert total2 == 3
            assert len(rows2) == 1
            assert {row.id for row in rows + rows2} == set(created)

            # 创建时间排序：方向可切换，非法方向显式拒绝
            ascending, _ = await memory_service.list_memories(
                db, q=tag, sort_dir="asc", limit=10, offset=0
            )
            ascending_times = [row.created_at for row in ascending]
            assert ascending_times == sorted(ascending_times), ascending_times

            descending, _ = await memory_service.list_memories(
                db, q=tag, sort_dir="desc", limit=10, offset=0
            )
            descending_times = [row.created_at for row in descending]
            assert descending_times == sorted(descending_times, reverse=True), descending_times

            with pytest.raises(ValueError):
                await memory_service.list_memories(db, q=tag, sort_dir="sideways")

            target = created[0]
            await memory_service.set_enabled(db, target, False)
            disabled, _ = await memory_service.list_memories(
                db, q=tag, enabled=False, limit=10, offset=0
            )
            assert [row.id for row in disabled] == [target]
            # 停用后语义回忆不应返回该条（向量仍在，enabled 过滤生效）
            hits = await memory_service.recall(db, query=tag, agent_id=None)
            assert all(h["id"] != target for h in hits)
            await memory_service.set_enabled(db, target, True)
    finally:
        await _cleanup(created)
        get_settings.cache_clear()


async def test_memory_api_filters_and_toggle(env: dict[str, str], api_base: str) -> None:
    """HTTP 层：q/分页参数真正生效，PATCH 能切换启停。"""

    tag = f"mem-api-{uuid.uuid4().hex[:8]}"
    seeded: list[str] = []
    try:
        async with SessionLocal() as db:
            # 管理 API 的筛选/启停不依赖向量；直接插行避免测试被嵌入服务抖动拖垮
            item = MemoryItem(
                id=str(uuid.uuid4()),
                content=f"{tag} 需要被搜索到",
                scope_agent_id=None,
                enabled=True,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)
            seeded.append(item.id)
            item_id = item.id

        async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
            if (await client.get("/healthz")).status_code != 200:
                pytest.skip("后端未就绪")
            token = await login_access_token(client, env)
            headers = {"Authorization": f"Bearer {token}"}
            listing = await client.get(
                f"/api/v1/memory?q={tag}&limit=5&offset=0", headers=headers
            )
            assert listing.status_code == 200, listing.text
            body = listing.json()
            assert body["total"] == 1
            assert body["items"][0]["id"] == item_id
            assert body["items"][0]["enabled"] is True

            patch = await client.patch(
                f"/api/v1/memory/{item_id}", headers=headers, json={"enabled": False}
            )
            assert patch.status_code == 200, patch.text
            assert patch.json()["enabled"] is False
            assert patch.json()["source_session_id"] is None
    finally:
        await _cleanup(seeded)


async def test_memory_hint_injects_enabled_scoped_items(monkeypatch, tmp_path) -> None:
    """人设记忆段：启用 +（全局 ∪ 本助理）按最近取；禁用与纯他助理不进。"""

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    created: list[str] = []
    try:
        async with SessionLocal() as db:
            mine = await memory_service.remember(
                db, content="用户的 Gitee 账号是 demo-user。", agent_id="default"
            )
            created.append(mine.id)
            glob = await memory_service.remember(
                db, content="全局偏好：回答用简体中文。"
            )
            created.append(glob.id)
            disabled = await memory_service.remember(
                db, content="已停用的旧记忆。", agent_id="default"
            )
            created.append(disabled.id)
            await memory_service.set_enabled(db, disabled.id, False)

            hint_default = await memory_service.compose_memory_hint(db, agent_id="default")
            assert "demo-user" in hint_default
            assert "简体中文" in hint_default
            assert "已停用" not in hint_default

            # 无助理语境只带全局记忆
            hint_global = await memory_service.compose_memory_hint(db, agent_id=None)
            assert "简体中文" in hint_global
            assert "demo-user" not in hint_global
    finally:
        await _cleanup(created)
