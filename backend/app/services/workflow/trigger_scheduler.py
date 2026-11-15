"""触发器调度循环：随应用 lifespan 启动的 asyncio 常驻任务。

每 30 秒扫一遍启用的 cron 触发器：到点（next_fire_at ≤ now）即派发执行并
重算下次触发时间；失败按 max_retries 指数退避重试。Webhook 触发不进循环，
由 HTTP 端点直接派发。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trigger import TriggerRule, TriggerRun
from app.services.workflow import trigger_executor
from app.services.platform.app_settings_service import get_system_timezone
from app.services.workflow.trigger_service import TriggerValidationError, compute_next_fire

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SECONDS = 30
_RETRY_BASE_SECONDS = 60
_RETRY_MAX_SECONDS = 3600

_task: asyncio.Task | None = None


async def start_scheduler() -> None:
    """启动调度循环；幂等（重复调用直接跳过）。"""

    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("触发器调度循环已启动 interval=%ss", _SCAN_INTERVAL_SECONDS)


async def stop_scheduler() -> None:
    """停止调度循环；应用关闭时调用。"""

    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    logger.info("触发器调度循环已停止")


async def _loop() -> None:
    while True:
        try:
            await _scan_once()
        except Exception as exc:  # noqa: BLE001 — 调度循环绝不退出
            logger.exception("触发器调度扫描异常: %s", exc)
        await asyncio.sleep(_SCAN_INTERVAL_SECONDS)


async def _scan_once() -> None:
    from app.db import SessionLocal

    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        try:
            _, tz = await get_system_timezone(db)
        except Exception as exc:  # noqa: BLE001 — 时区缺失不能停掉调度
            logger.warning("读取系统时区失败，按 UTC 解释 cron: %s", exc)
            tz = timezone.utc
        rules = list(
            (
                await db.scalars(
                    select(TriggerRule).where(
                        TriggerRule.enabled.is_(True), TriggerRule.kind == "cron"
                    )
                )
            ).all()
        )
        due: list[TriggerRule] = []
        for rule in rules:
            # 单条规则数据有问题时不能拖垮整轮扫描。
            try:
                if rule.next_fire_at is None:
                    # 老数据/调度器首次接管：初始化下次触发时间，本轮不执行
                    rule.next_fire_at = compute_next_fire(
                        rule.cron_expr, now, tz
                    )
                    continue
                if rule.next_fire_at <= now:
                    due.append(rule)
            except TriggerValidationError as exc:
                logger.error(
                    "触发器 cron 非法，跳过 trigger_id=%s expr=%r error=%s",
                    rule.id,
                    rule.cron_expr,
                    exc,
                )

        for rule in due:
            try:
                # 失败重试检查：最近一次失败且 attempt 未超上限、且已过退避窗口
                await _maybe_retry_failed(db, rule, now)
                rule.next_fire_at = compute_next_fire(
                    rule.cron_expr, datetime.now(timezone.utc), tz
                )
            except Exception as exc:  # noqa: BLE001 — 单条规则失败不阻塞其他规则
                logger.exception(
                    "触发器预处理失败 trigger_id=%s error=%s", rule.id, exc
                )

        await db.commit()
        for rule in due:
            try:
                await trigger_executor.fire_rule(db, rule)
            except Exception as exc:  # noqa: BLE001 — 一样只影响这一条
                logger.exception(
                    "触发器派发失败 trigger_id=%s error=%s", rule.id, exc
                )


def _retry_delay_seconds(attempt: int) -> int:
    """指数退避：1 次失败后 60s，之后 120s、240s……最长 1 小时。"""

    exponent = max(0, attempt - 1)
    return min(_RETRY_BASE_SECONDS * (2**exponent), _RETRY_MAX_SECONDS)


async def _maybe_retry_failed(
    db: AsyncSession, rule: TriggerRule, now: datetime
) -> None:
    """失败重试：attempt ≤ max_retries 且指数退避窗口已过再执行。

    通过在派发前检查最近一次 TriggerRun，失败的触发会以 attempt+1 重跑；
    成功/等待中的触发不重试。
    """

    recent = (
        await db.scalars(
            select(TriggerRun)
            .where(TriggerRun.trigger_id == rule.id)
            .order_by(TriggerRun.id.desc())
            .limit(1)
        )
    ).first()
    if recent is None or recent.status != "failed":
        return
    if recent.attempt > rule.max_retries:
        return
    if recent.finished_at is None:
        return
    backoff = _retry_delay_seconds(recent.attempt)
    if recent.finished_at + timedelta(seconds=backoff) > now:
        return
    logger.info(
        "触发器重试 trigger_id=%s attempt=%s delay=%ss",
        rule.id,
        recent.attempt + 1,
        backoff,
    )
    await trigger_executor.fire_rule(db, rule, attempt=recent.attempt + 1)
