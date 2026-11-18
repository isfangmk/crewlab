"""默认模型选择：default_provider 缺失/失效时回退到首个启用 provider。

修复根因：删除/禁用默认供应商会清掉 default_provider 设置项，但库里仍有其它
启用的 provider；此前 get_default_selection 抛错让整个模型目录返回 409，前端
显示「缺少 default_provider 设置」。这里验证回退口径。用临时 SQLite 真库，
不碰用户数据。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_db_engine
from app.models.base import Base
from app.models.llm import AppSetting, LlmProvider
from app.services.platform.llm_config import get_default_selection


def _provider(provider_id: str, enabled: bool) -> LlmProvider:
    return LlmProvider(
        provider_id=provider_id,
        label=provider_id,
        base_url="http://example.invalid",
        api_key="k",
        models="m1,m2",
        default_model="m1",
        enabled=enabled,
    )


def _sessionmaker(tmp_path):
    engine = create_db_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_fallback_when_default_setting_missing(tmp_path) -> None:
    """default_provider 设置项缺失：回退到首个启用的 provider，不抛错。"""
    engine, sm = _sessionmaker(tmp_path / "missing.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sm() as db:
            db.add(_provider("first", enabled=True))
            await db.commit()
            assert await get_default_selection(db) == ("first", "m1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fallback_when_default_points_to_disabled(tmp_path) -> None:
    """default_provider 指向已禁用 provider：回退到首个启用 provider。"""
    engine, sm = _sessionmaker(tmp_path / "disabled.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sm() as db:
            db.add(_provider("deleted", enabled=False))
            db.add(_provider("alive", enabled=True))
            db.add(AppSetting(key="default_provider", value="deleted"))
            await db.commit()
            assert await get_default_selection(db) == ("alive", "m1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uses_valid_default_setting(tmp_path) -> None:
    """default_provider 指向启用的 provider：按偏好返回，不回退。"""
    engine, sm = _sessionmaker(tmp_path / "valid.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sm() as db:
            db.add(_provider("a", enabled=True))
            db.add(_provider("b", enabled=True))
            db.add(AppSetting(key="default_provider", value="b"))
            await db.commit()
            assert await get_default_selection(db) == ("b", "m1")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_enabled_provider_still_fails(tmp_path) -> None:
    """没有任何启用 provider 时仍显式失败（无模型可选）。"""
    engine, sm = _sessionmaker(tmp_path / "empty.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sm() as db:
            db.add(_provider("off", enabled=False))
            await db.commit()
            with pytest.raises(LookupError, match="配置-模型为空"):
                await get_default_selection(db)
    finally:
        await engine.dispose()
