"""触发器调度循环：随应用 lifespan 启动的 asyncio 常驻任务。

每 30 秒扫一遍启用的 cron 触发器：到点（next_fire_at ≤ now）即派发执行并
重算下次触发时间；失败按 max_retries 在下一轮延迟重试（attempt × 60 秒）。
Webhook 触发不进循环，由 HTTP 端点直接派发。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models_orm.trigger import TriggerRule, TriggerRun
from app.services import trigger_executor

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SECONDS = 30
_RETRY_BASE_SECONDS = 60

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
            if rule.next_fire_at is None:
                # 老数据/调度器首次接管：初始化下次触发时间，本轮不执行
                rule.next_fire_at = trigger_executor.compute_next_fire(
                    rule.cron_expr, now
                )
                continue
            if rule.next_fire_at <= now:
                due.append(rule)

        for rule in due:
            # 失败重试检查：最近一次失败且 attempt 未超上限、且已过退避窗口
            await _maybe_retry_failed(db, rule, now)
            rule.next_fire_at = trigger_executor.compute_next_fire(
                rule.cron_expr, datetime.now(timezone.utc)
            )

        await db.commit()
        for rule in due:
            await trigger_executor.fire_rule(db, rule)


async def _maybe_retry_failed(
    db: AsyncSession, rule: TriggerRule, now: datetime
) -> None:
    """失败重试：attempt ≤ max_retries 且退避窗口（attempt × 60s）已过再执行。

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
    backoff = _RETRY_BASE_SECONDS * recent.attempt
    if recent.finished_at is None:
        return
    if recent.finished_at + timedelta(seconds=backoff) > now:
        return
    logger.info(
        "触发器重试 trigger_id=%s attempt=%s", rule.id, recent.attempt + 1
    )
    await trigger_executor.fire_rule(db, rule, attempt=recent.attempt + 1)
