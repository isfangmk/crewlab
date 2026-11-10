"""通用键值设置：agent 运行参数 + 系统时区。"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.llm import AppSetting

logger = logging.getLogger(__name__)

_AGENT_KEYS = frozenset(
    {
        "write_enabled",
        "llm_timeout_seconds",
        "output_locale",
        "runtime_version",
    }
)

# 固定偏移，避免依赖系统 IANA 时区库；键名落在 observability.timezone
SYSTEM_TIMEZONES: dict[str, timezone] = {
    "UTC": timezone.utc,
    "UTC+00:00": timezone.utc,
    "UTC+05:30": timezone(timedelta(hours=5, minutes=30)),
    "UTC+08:00": timezone(timedelta(hours=8)),
    "UTC+09:00": timezone(timedelta(hours=9)),
    "UTC-05:00": timezone(timedelta(hours=-5)),
    "UTC-08:00": timezone(timedelta(hours=-8)),
}
DEFAULT_SYSTEM_TIMEZONE = "UTC+08:00"
_OBSERVABILITY_KEYS = frozenset({"timezone"})
_KNOWLEDGE_KEYS = frozenset(
    {"chunk_size", "chunk_overlap", "approval_timeout_hours", "rrf_min_score"}
)
_SUPPORTED_CATEGORIES = frozenset({"agent", "observability", "knowledge"})


def _setting_pk(category: str, key: str) -> str:
    return f"{category}.{key}"


def _parse_pk(pk: str) -> tuple[str, str]:
    if "." not in pk:
        raise ValueError(f"非法设置主键: {pk}")
    category, key = pk.split(".", 1)
    if category == "" or key == "":
        raise ValueError(f"非法设置主键: {pk}")
    return category, key


async def list_category(db: AsyncSession, category: str) -> list[dict]:
    if category not in _SUPPORTED_CATEGORIES:
        raise ValueError(f"不支持的 category: {category}")
    prefix = f"{category}."
    rows = (
        await db.scalars(select(AppSetting).where(AppSetting.key.startswith(prefix)))
    ).all()
    items: list[dict] = []
    for row in rows:
        _cat, key = _parse_pk(row.key)
        items.append(
            {
                "category": category,
                "key": key,
                "value": row.value,
                "sensitive": False,
            }
        )
    return items


_AGENT_RUNTIME_SEEDS: dict[str, str] = {
    "write_enabled": "false",
    "llm_timeout_seconds": "120",
    "output_locale": "",
}

_KNOWLEDGE_SEEDS: dict[str, str] = {
    "chunk_size": "800",
    "chunk_overlap": "100",
    "approval_timeout_hours": "72",
    "rrf_min_score": "0",
}


async def ensure_knowledge_settings(db: AsyncSession) -> None:
    """种子写入知识库/审批超时参数；已存在的键不动。"""

    added = 0
    for key, value in _KNOWLEDGE_SEEDS.items():
        pk = _setting_pk("knowledge", key)
        row = await db.get(AppSetting, pk)
        if row is not None:
            continue
        db.add(AppSetting(key=pk, value=value))
        added += 1
    if added > 0:
        await db.commit()
        logger.info("已写入知识库参数种子 count=%s", added)


async def ensure_agent_runtime(db: AsyncSession) -> None:
    """种子写入助理运行参数；已存在的键不动。"""

    added = 0
    for key, value in _AGENT_RUNTIME_SEEDS.items():
        pk = _setting_pk("agent", key)
        row = await db.get(AppSetting, pk)
        if row is not None:
            continue
        db.add(AppSetting(key=pk, value=value))
        added += 1
    if added > 0:
        await db.commit()
        logger.info("已写入助理运行参数种子 count=%s", added)


async def get_agent_runtime(db: AsyncSession) -> dict[str, str | bool | int]:
    """读取助理运行参数；缺键或非法值显式失败。"""

    items = {i["key"]: i["value"] for i in await list_category(db, "agent")}
    write_raw = items.get("write_enabled")
    if write_raw is None:
        raise ValueError("缺少 agent.write_enabled")
    if write_raw not in ("true", "false"):
        raise ValueError(f"write_enabled 非法: {write_raw}")
    write_enabled = write_raw == "true"

    timeout_raw = items.get("llm_timeout_seconds")
    if timeout_raw is None:
        raise ValueError("缺少 agent.llm_timeout_seconds")
    llm_timeout_seconds = int(timeout_raw)
    if llm_timeout_seconds < 30 or llm_timeout_seconds > 900:
        raise ValueError(f"llm_timeout_seconds 非法: {timeout_raw}")

    locale = items.get("output_locale")
    if locale is None:
        raise ValueError("缺少 agent.output_locale")
    if locale not in ("", "zh", "en"):
        raise ValueError(f"output_locale 非法: {locale}")

    return {
        "write_enabled": write_enabled,
        "llm_timeout_seconds": llm_timeout_seconds,
        "output_locale": locale,
    }


async def ensure_system_timezone(db: AsyncSession) -> None:
    """种子写入系统时区；已存在则不动。"""

    pk = _setting_pk("observability", "timezone")
    row = await db.get(AppSetting, pk)
    if row is not None:
        return
    db.add(AppSetting(key=pk, value=DEFAULT_SYSTEM_TIMEZONE))
    await db.commit()
    logger.info("已写入系统时区种子 value=%s", DEFAULT_SYSTEM_TIMEZONE)


async def get_system_timezone(db: AsyncSession) -> tuple[str, timezone]:
    """返回 (配置名, tzinfo)。缺配置显式失败。"""

    pk = _setting_pk("observability", "timezone")
    row = await db.get(AppSetting, pk)
    if row is None:
        raise ValueError("缺少 observability.timezone")
    name = row.value
    tz = SYSTEM_TIMEZONES.get(name)
    if tz is None:
        raise ValueError(f"非法 observability.timezone: {name}")
    return name, tz


async def put_setting(
    db: AsyncSession,
    *,
    category: str,
    key: str,
    value: str,
    sensitive: bool | None,
) -> dict:
    if category not in _SUPPORTED_CATEGORIES:
        raise ValueError(f"不支持的 category: {category}")
    if sensitive is True:
        raise ValueError(f"{category} 设置不允许 sensitive=true")

    if category == "agent":
        if key not in _AGENT_KEYS:
            raise ValueError(f"不支持的 agent key: {key}")
        if key == "write_enabled" and value not in ("true", "false"):
            raise ValueError("write_enabled 只能是 true/false")
        if key == "llm_timeout_seconds":
            seconds = int(value)
            if seconds < 30 or seconds > 900:
                raise ValueError("llm_timeout_seconds 范围是 30..900")
        if key == "output_locale" and value not in ("", "zh", "en"):
            raise ValueError("output_locale 只能是空串 / zh / en")
        if key == "runtime_version" and value == "":
            raise ValueError("runtime_version 不能为空")
    elif category == "observability":
        if key not in _OBSERVABILITY_KEYS:
            raise ValueError(f"不支持的 observability key: {key}")
        if key == "timezone" and value not in SYSTEM_TIMEZONES:
            raise ValueError(
                "timezone 非法，允许: " + ", ".join(sorted(SYSTEM_TIMEZONES))
            )
    elif category == "knowledge":
        if key not in _KNOWLEDGE_KEYS:
            raise ValueError(f"不支持的 knowledge key: {key}")
        if key == "rrf_min_score":
            try:
                score = float(value)
            except ValueError as exc:
                raise ValueError("rrf_min_score 必须是数字") from exc
            if not math.isfinite(score) or score < 0:
                raise ValueError("rrf_min_score 必须 >= 0")
        if key in ("chunk_size", "chunk_overlap", "approval_timeout_hours"):
            number = int(value)
            if key == "chunk_size" and number < 64:
                raise ValueError("chunk_size 必须 >= 64")
            if key == "chunk_overlap" and number < 0:
                raise ValueError("chunk_overlap 必须 >= 0")
            if key == "approval_timeout_hours" and number < 1:
                raise ValueError("approval_timeout_hours 必须 >= 1")

    pk = _setting_pk(category, key)
    row = await db.get(AppSetting, pk)
    if row is None:
        row = AppSetting(key=pk, value=value)
        db.add(row)
    else:
        row.value = value
    await db.commit()
    logger.info("写入设置 category=%s key=%s", category, key)
    return {
        "category": category,
        "key": key,
        "value": value,
        "sensitive": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
