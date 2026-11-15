"""用量与成本聚合：手动单价、日界时区、软限额与缺价目口径。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_db_engine
from app.models.base import Base
from app.models.llm import AppSetting, LlmProvider
from app.models.observation import ObservationTurn
from app.services.observability.usage_service import parse_model_prices, usage_summary


def _new_sessionmaker(db_path):
    """每个用例独立临时 SQLite 文件库（pytest tmp_path 自动清理），不碰用户数据。"""

    engine = create_db_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_settings(db) -> None:
    db.add(AppSetting(key="observability.timezone", value="UTC+08:00"))
    await db.commit()


def _provider(provider_id: str, prices: dict | None) -> LlmProvider:
    return LlmProvider(
        provider_id=provider_id,
        label=provider_id,
        base_url="http://example.invalid",
        api_key="k",
        models="model-a,model-b",
        default_model="model-a",
        enabled=True,
        model_prices_json=json.dumps(prices) if prices is not None else None,
    )


def _turn(model: str, agent: str, started_at: datetime, prompt: int, completion: int) -> ObservationTurn:
    return ObservationTurn(
        id=f"t_{uuid.uuid4().hex[:10]}",
        session_id="s-1",
        agent_id=agent,
        provider="p",
        model=model,
        status="ok",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        duration_ms=1000,
        started_at=started_at,
    )


def test_parse_model_prices_contract() -> None:
    """合法条目归一为 (输入, 输出)；缺侧按 0；非法结构显式失败。"""

    assert parse_model_prices(None) == {}
    assert parse_model_prices("") == {}
    parsed = parse_model_prices('{"m": {"input_per_1m": 3.5}}')
    assert parsed == {"m": (3.5, 0.0)}
    parsed = parse_model_prices('{"m": {"input_per_1m": 3, "output_per_1m": 7}}')
    assert parsed == {"m": (3.0, 7.0)}

    with pytest.raises(ValueError):
        parse_model_prices("{bad json")
    with pytest.raises(ValueError):
        parse_model_prices("[]")
    with pytest.raises(ValueError):
        parse_model_prices('{"m": {"input_per_1m": -1}}')
    with pytest.raises(ValueError):
        parse_model_prices('{"m": {}}')
    with pytest.raises(ValueError):
        parse_model_prices('{"m": {"input_per_1m": "x"}}')


@pytest.mark.asyncio
async def test_usage_summary_costs_and_unpriced(tmp_path) -> None:
    """价目模型计成本；缺价目模型 cost=None 并计未计价回合，不按 0 计。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "usage.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker() as db:
            await _seed_settings(db)
            # model-a 有价（输入 3.5 / 输出 7 每 1M）；model-b 无价目
            db.add(
                _provider(
                    "p1",
                    {"model-a": {"input_per_1m": 3.5, "output_per_1m": 7.0}},
                )
            )
            t0 = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)
            db.add(_turn("model-a", "agent-x", t0, 1_000_000, 500_000))
            db.add(_turn("model-a", "agent-x", t0, 2_000_000, 500_000))
            db.add(_turn("model-b", "agent-y", t0, 1_000_000, 1_000_000))
            await db.commit()

            result = await usage_summary(db)
            stats = result["stats"]
            assert stats["turns"] == 3
            assert stats["prompt_tokens"] == 4_000_000
            assert stats["completion_tokens"] == 2_000_000
            assert stats["total_tokens"] == 6_000_000
            # 只有 model-b 的 1 个回合未计价：总成本半真不给，记 None
            assert stats["cost"] is None
            assert stats["unpriced_turns"] == 1
            assert stats["unpriced_tokens"] == 2_000_000

            by_model = {row["key"]: row for row in result["by_model"]}
            # model-a: 3.5*3 + 7*1 = 17.5（每 1M 单价 × 百万数）
            assert by_model["model-a"]["cost"] == pytest.approx(17.5)
            assert by_model["model-a"]["unpriced_turns"] == 0
            assert by_model["model-b"]["cost"] is None
            assert by_model["model-b"]["unpriced_turns"] == 1
            # 按总 token 降序：model-a 5M 在前
            assert [r["key"] for r in result["by_model"]] == ["model-a", "model-b"]

            by_agent = {row["key"]: row for row in result["by_agent"]}
            assert by_agent["agent-x"]["cost"] == pytest.approx(17.5)
            assert by_agent["agent-y"]["cost"] is None

            # 按日聚合跨模型混价：当天含未计价回合 → cost=None
            assert len(result["by_day"]) == 1
            assert result["by_day"][0]["cost"] is None
            assert result["by_day"][0]["total_tokens"] == 6_000_000
            assert result["facets"]["models"] == ["model-a", "model-b"]
            assert result["facets"]["agents"] == ["agent-x", "agent-y"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_summary_day_bucket_uses_configured_timezone(tmp_path) -> None:
    """日界按配置时区（UTC+8）切：17:30Z 落到次日，而不是 UTC 当天。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "usage.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker() as db:
            await _seed_settings(db)
            # 2026-11-14 17:30Z = 2026-11-15 01:30 (+8)
            db.add(
                _turn(
                    "model-a",
                    "agent-x",
                    datetime(2026, 11, 14, 17, 30, tzinfo=timezone.utc),
                    10,
                    10,
                )
            )
            await db.commit()

            result = await usage_summary(db)
            assert [row["key"] for row in result["by_day"]] == ["2026-11-15"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_summary_soft_limit_and_filters(tmp_path) -> None:
    """软限额只提醒（读数透出）；时间/模型过滤生效；起点晚于终点显式报错。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "usage.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker() as db:
            await _seed_settings(db)
            db.add(AppSetting(key="usage.daily_token_soft_limit", value="50"))
            # 写入口径是 UTC 墙钟（见 models/base.AwareDateTime 注释）；同一时刻在 +8 的日界由聚合侧切
            now = datetime.now(timezone.utc)
            db.add(_turn("model-a", "agent-x", now, 30, 20))
            db.add(_turn("model-b", "agent-x", datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc), 999, 999))
            await db.commit()

            result = await usage_summary(db)
            assert result["soft_limit_tokens"] == 50
            assert result["today_tokens"] == 50

            only_model_b = await usage_summary(db, model="model-b")
            assert only_model_b["stats"]["turns"] == 1
            # 今日用量不跟随筛选：软限额看的是全站今天
            assert only_model_b["today_tokens"] == 50

            ranged = await usage_summary(
                db,
                started_from=datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc),
                started_to=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
            )
            assert ranged["stats"]["turns"] == 1
            assert ranged["stats"]["prompt_tokens"] == 999

            with pytest.raises(ValueError):
                await usage_summary(
                    db,
                    started_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                    started_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
    finally:
        await engine.dispose()
