"""触发器执行分发：按目标类型发起工作流运行或助理会话。

每次执行写一条 TriggerRun 留痕；失败按 max_retries 由调度器延迟重试。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.trigger import TriggerRule, TriggerRun
from app.services import workflow_runner
from app.services.audit_service import write_audit

logger = logging.getLogger(__name__)


def trigger_payload(rule: TriggerRule) -> dict[str, object]:
    """workflow 触发的输入值：values_json 解析失败按空输入处理，失败会留痕在 TriggerRun。"""

    import json

    try:
        parsed = json.loads(rule.values_json or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finish(run: TriggerRun, *, status: str, error: str = "", ref_id: str | None = None) -> None:
    run.status = status
    run.error = error[:2000]
    if ref_id is not None:
        run.ref_id = ref_id
    run.finished_at = datetime.now(timezone.utc)


async def fire_rule(db: AsyncSession, rule: TriggerRule, *, attempt: int = 1) -> TriggerRun:
    """执行一次触发：按目标类型分发；无论成败都写 TriggerRun。"""

    run = TriggerRun(trigger_id=rule.id, attempt=attempt, status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    logger.info(
        "触发器执行 trigger_id=%s name=%s target=%s attempt=%s",
        rule.id,
        rule.name,
        rule.target_kind,
        attempt,
    )
    error = ""
    ref_id = ""
    if rule.target_kind == "workflow":
        if rule.workflow_id is None:
            error = "触发器缺少 workflow_id"
        else:
            try:
                ref_id = await workflow_runner.start_run(
                    workflow_id=rule.workflow_id,
                    payload=trigger_payload(rule),
                    triggered_by=f"trigger:{rule.name}",
                    source="published",
                )
            except Exception as exc:  # noqa: BLE001 — 触发失败要进留痕而不是打断调度循环
                error = f"{type(exc).__name__}: {exc}"
    else:
        try:
            ref_id, error = await _run_agent_turn(db, rule)
        except Exception as exc:  # noqa: BLE001 — 同上，留痕优先
            error = f"{type(exc).__name__}: {exc}"

    if rule.target_kind == "workflow" and ref_id and error == "":
        # 工作流执行是后台任务：TriggerRun 记录发起成功，最终状态由运行记录承载
        _finish(run, status="succeeded", ref_id=ref_id)
    elif error:
        _finish(run, status="failed", error=error, ref_id=ref_id)
    else:
        _finish(run, status="succeeded", ref_id=ref_id)

    rule.last_fired_at = datetime.now(timezone.utc)
    await db.commit()
    await write_audit(
        actor_user_id=None,
        action="trigger.fire",
        resource_type="trigger",
        resource_id=str(rule.id),
        detail={
            "name": rule.name,
            "target_kind": rule.target_kind,
            "attempt": attempt,
            "status": run.status,
            "ref_id": run.ref_id,
            "error": run.error[:500],
        },
    )
    logger.info(
        "触发器完成 trigger_id=%s status=%s ref=%s error=%s",
        rule.id,
        run.status,
        run.ref_id,
        run.error[:120],
    )
    return run


async def _run_agent_turn(db: AsyncSession, rule: TriggerRule) -> tuple[str, str]:
    """以目标助理发起一轮真实对话（进程内消费全部帧），返回 (session_id, error)。"""

    from app.request_context import set_request_id
    from app.services import chat_service
    from app.services.llm_config import list_enabled_providers

    providers = await list_enabled_providers(db)
    if not providers:
        return "", "没有启用的模型 provider，无法发起触发式会话"
    provider = providers[0]

    set_request_id(f"trigger-{rule.id}-{datetime.now(timezone.utc).timestamp()}")
    session = await chat_service.create_session(
        db,
        title=f"[自动] {rule.name}",
        provider=provider.provider_id,
        model=provider.default_model,
        agent_id=rule.agent_id or "default",
        user_id=f"trigger:{rule.id}",
    )
    await db.commit()

    frames = 0
    error = ""
    async for frame in chat_service.stream_session_message(
        db,
        session_id=session.id,
        content=rule.prompt,
        provider=None,
        model=None,
        client_message_id=None,
        thinking_enabled=False,
        web_search_enabled=False,
        attachment_ids=[],
        locale=None,
        mentions=None,
    ):
        frames += 1
        if frame.event == "approval_pending":
            error = "回合等待人工审批后中止；无人值守触发器建议绑定只读工具的助理"
        elif frame.event == "error":
            payload = frame.data if isinstance(frame.data, dict) else {}
            error = str(payload.get("error", "回合失败"))
    if error:
        return session.id, error
    logger.info("触发式会话完成 trigger_id=%s session=%s frames=%s", rule.id, session.id, frames)
    return session.id, ""
