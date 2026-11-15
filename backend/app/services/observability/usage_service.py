"""用量与成本聚合：observation_turns 按日/模型/助理汇总，成本按手动单价估算。

数据源是 observation_turns（逐回合、带模型/助理/时间索引列），
chat_messages.stats_json 只用于气泡展示，不进聚合。
单价只认 llm_providers.model_prices_json 的手动价（用户选定不读 litellm 价目表）；
缺价目的模型成本记 None（前端显示「未计价」），绝不按 0 计。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm import AppSetting, LlmProvider
from app.models.observation import ObservationTurn
from app.services.platform.app_settings_service import get_system_timezone

logger = logging.getLogger(__name__)

# 手动单价表条目：<模型名>: {"input_per_1m": <float>, "output_per_1m": <float>}
# 两侧每百万 token 单价；至少填一侧，缺侧按 0 计（管理员的选择，不是系统虚构）。
ModelPrice = tuple[float, float]


def parse_model_prices(raw: str | None) -> dict[str, ModelPrice]:
    """解析手动单价表；非法结构显式失败，不静默吞掉配置错误。"""

    if raw is None or raw.strip() == "":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model_prices_json 不是合法 JSON: {raw[:120]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model_prices_json 必须是 JSON 对象")
    prices: dict[str, ModelPrice] = {}
    for model, entry in parsed.items():
        if not isinstance(model, str) or model == "":
            raise ValueError(f"model_prices_json 存在非法模型名: {model!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"model_prices_json 的 {model} 必须是对象")
        values: list[float] = []
        for side in ("input_per_1m", "output_per_1m"):
            v = entry.get(side)
            if v is None:
                continue
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"model_prices_json 的 {model}.{side} 必须是数字")
            if v < 0:
                raise ValueError(f"model_prices_json 的 {model}.{side} 不能为负")
            values.append(float(v))
        if not values:
            raise ValueError(f"model_prices_json 的 {model} 至少要填一侧单价")
        prices[model] = (
            float(entry.get("input_per_1m", 0) or 0),
            float(entry.get("output_per_1m", 0) or 0),
        )
    return prices


async def load_price_map(db: AsyncSession) -> dict[str, ModelPrice]:
    """合并全部 provider 的手动单价表；同名模型先命中者优先（按 provider id 升序）。"""

    rows = (
        await db.scalars(
            select(LlmProvider).where(LlmProvider.model_prices_json.is_not(None)).order_by(LlmProvider.id)
        )
    ).all()
    merged: dict[str, ModelPrice] = {}
    for row in rows:
        for model, price in parse_model_prices(row.model_prices_json).items():
            if model in merged:
                logger.warning(
                    "模型 %s 在多个 provider 配了单价，先命中者优先 provider_id=%s",
                    model,
                    row.provider_id,
                )
                continue
            merged[model] = price
    return merged


def price_for(prices: dict[str, ModelPrice], model: str) -> ModelPrice | None:
    """按模型名精确匹配手动单价；缺价目返回 None（未计价）。"""

    return prices.get(model)


def _cost_of(price: ModelPrice | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """缺价目返回 None——未计价不是 0 成本。"""

    if price is None:
        return None
    input_per_1m, output_per_1m = price
    return prompt_tokens * input_per_1m / 1_000_000 + completion_tokens * output_per_1m / 1_000_000


def _day_expr(dialect_name: str, offset_minutes: int):
    """按配置时区的固定偏移切日界；SQLite/PG 双方言（与 summarize_turns 同款分支）。"""

    if dialect_name == "postgresql":
        return func.to_char(
            ObservationTurn.started_at + text(f"interval '{offset_minutes} minutes'"),
            "YYYY-MM-DD",
        )
    return func.strftime(
        "%Y-%m-%d", ObservationTurn.started_at, f"{offset_minutes:+d} minutes"
    )


class _Acc:
    """一个分组的累计量；duration 用 sum/count 再平均，避免组间平均再平均。"""

    __slots__ = (
        "turns",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "duration_sum",
        "duration_n",
        "cost_sum",
        "unpriced_turns",
        "unpriced_tokens",
    )

    def __init__(self) -> None:
        self.turns = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.duration_sum = 0
        self.duration_n = 0
        self.cost_sum = 0.0
        self.unpriced_turns = 0
        self.unpriced_tokens = 0

    def add(
        self,
        *,
        turns: int,
        prompt: int,
        completion: int,
        total: int,
        duration_sum: int | None,
        duration_n: int,
        cost: float | None,
    ) -> None:
        self.turns += turns
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.duration_sum += duration_sum or 0
        self.duration_n += duration_n
        if cost is None:
            self.unpriced_turns += turns
            self.unpriced_tokens += total
        else:
            self.cost_sum += cost

    def row(self, key: str) -> dict:
        # 有任何未计价回合就不出成本合计：宁可「—」也不给半真数字
        return {
            "key": key,
            "turns": self.turns,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "duration_avg_ms": (self.duration_sum / self.duration_n) if self.duration_n else None,
            "cost": self.cost_sum if self.unpriced_turns == 0 else None,
            "unpriced_turns": self.unpriced_turns,
        }


async def usage_summary(
    db: AsyncSession,
    *,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    channel: str | None = None,
) -> dict:
    """按 日×模型×助理×渠道 一次分组取数，Python 侧滚动三视角并计成本。"""

    if started_from is not None and started_to is not None and started_from > started_to:
        raise ValueError("started_from 不能晚于 started_to")

    prices = await load_price_map(db)
    _, tz = await get_system_timezone(db)
    offset_minutes = int((tz.utcoffset(None) or timedelta()).total_seconds() // 60)
    day_expr = _day_expr(db.get_bind().dialect.name, offset_minutes)
    day = day_expr.label("day")

    filters = []
    if started_from is not None:
        filters.append(ObservationTurn.started_at >= started_from)
    if started_to is not None:
        filters.append(ObservationTurn.started_at <= started_to)
    if agent_id is not None:
        filters.append(ObservationTurn.agent_id == agent_id)
    if model is not None:
        filters.append(ObservationTurn.model == model)
    if channel is not None:
        filters.append(ObservationTurn.channel == channel)

    stmt = (
        select(
            day,
            ObservationTurn.model,
            ObservationTurn.agent_id,
            ObservationTurn.channel,
            func.count().label("turns"),
            func.coalesce(func.sum(ObservationTurn.prompt_tokens), 0),
            func.coalesce(func.sum(ObservationTurn.completion_tokens), 0),
            func.coalesce(func.sum(ObservationTurn.total_tokens), 0),
            func.sum(ObservationTurn.duration_ms),
            func.count(ObservationTurn.duration_ms),
        )
        .where(*filters)
        .group_by(day, ObservationTurn.model, ObservationTurn.agent_id, ObservationTurn.channel)
    )
    rows = (await db.execute(stmt)).all()

    by_day: dict[str, _Acc] = {}
    by_model: dict[str, _Acc] = {}
    by_agent: dict[str, _Acc] = {}
    total = _Acc()
    models_seen: set[str] = set()
    agents_seen: set[str] = set()
    channels_seen: set[str] = set()
    for row in rows:
        day_key, model_key, agent_key, channel_key = row[0], row[1], row[2], row[3]
        models_seen.add(model_key)
        agents_seen.add(agent_key)
        channels_seen.add(channel_key)
        turns, prompt, completion, total_tokens = row[4], row[5], row[6], row[7]
        duration_sum, duration_n = row[8], row[9]
        cost = _cost_of(price_for(prices, model_key), prompt, completion)
        for bucket, key in ((by_day, day_key), (by_model, model_key), (by_agent, agent_key)):
            bucket.setdefault(key, _Acc()).add(
                turns=turns,
                prompt=prompt,
                completion=completion,
                total=total_tokens,
                duration_sum=duration_sum,
                duration_n=duration_n,
                cost=cost,
            )
        total.add(
            turns=turns,
            prompt=prompt,
            completion=completion,
            total=total_tokens,
            duration_sum=duration_sum,
            duration_n=duration_n,
            cost=cost,
        )

    stats = total.row("stats")
    # 总计行不是分组行：去掉分组键，另补未计价 token 数
    stats.pop("key", None)
    stats["unpriced_tokens"] = total.unpriced_tokens

    # 今日用量与软限额：超限只提醒不拦截（软）。
    # 今日口径不跟随筛选条件——软限额看的是全站今天，不是筛选后的小集合。
    soft_limit_raw = await db.get(AppSetting, "usage.daily_token_soft_limit")
    soft_limit = (
        int(soft_limit_raw.value)
        if soft_limit_raw is not None and soft_limit_raw.value != ""
        else None
    )
    today_stmt = select(func.coalesce(func.sum(ObservationTurn.total_tokens), 0)).where(
        day_expr == datetime.now(tz).strftime("%Y-%m-%d")
    )
    today_tokens = int((await db.execute(today_stmt)).scalar_one())

    return {
        "stats": stats,
        "by_day": [by_day[k].row(k) for k in sorted(by_day)],
        "by_model": [
            by_model[k].row(k)
            for k in sorted(by_model, key=lambda k: (-by_model[k].total_tokens, k))
        ],
        "by_agent": [
            by_agent[k].row(k)
            for k in sorted(by_agent, key=lambda k: (-by_agent[k].total_tokens, k))
        ],
        # 筛选下拉的候选值：取当前结果集里出现过的维度值
        "facets": {
            "models": sorted(models_seen),
            "agents": sorted(agents_seen),
            "channels": sorted(channels_seen),
        },
        "soft_limit_tokens": soft_limit,
        "today_tokens": today_tokens,
    }
