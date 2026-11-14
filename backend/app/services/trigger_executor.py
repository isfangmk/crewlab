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
    """workflow 触发的输入值；JSON 不合法时显式失败并记进 TriggerRun。"""

    import json

    try:
        parsed = json.loads(rule.values_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"values_json 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("values_json 必须是 JSON 对象")
    return parsed


def _finish(run: TriggerRun, *, status: str, error: str = "", ref_id: str | None = None) -> None:
    run.status = status
    run.error = error[:2000]
    if ref_id is not None:
        run.ref_id = ref_id
    run.finished_at = datetime.now(timezone.utc)


async def fire_rule(
    db: AsyncSession,
    rule: TriggerRule,
    *,
    attempt: int = 1,
    actor_user_id: int | None = None,
) -> TriggerRun:
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
    status = "succeeded"
    if rule.target_kind == "workflow":
        if rule.workflow_id is None:
            error = "触发器缺少 workflow_id"
            status = "failed"
        else:
            try:
                ref_id = await workflow_runner.start_run(
                    workflow_id=rule.workflow_id,
                    payload=trigger_payload(rule),
                    # 稳定标识：名称可以改，运行溯源不能跟着变
                    triggered_by=f"trigger:{rule.id}",
                    source="published",
                )
            except Exception as exc:  # noqa: BLE001 — 触发失败要进留痕而不是打断调度循环
                error = f"{type(exc).__name__}: {exc}"
                status = "failed"
    else:
        try:
            ref_id, error, status = await _run_agent_turn(db, rule)
        except Exception as exc:  # noqa: BLE001 — 同上，留痕优先
            error = f"{type(exc).__name__}: {exc}"
            status = "failed"

    if rule.target_kind == "workflow" and ref_id and error == "":
        # 工作流执行是后台任务：TriggerRun 记录发起成功，最终状态由运行记录承载
        status = "succeeded"
    _finish(run, status=status, error=error, ref_id=ref_id)

    rule.last_fired_at = datetime.now(timezone.utc)
    await db.commit()
    await write_audit(
        actor_user_id=actor_user_id,
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


async def _run_agent_turn(
    db: AsyncSession, rule: TriggerRule
) -> tuple[str, str, str]:
    """以目标助理发起一轮真实对话，返回 (session_id, error, status)。

    停在人工审批时立即停止消费，把运行标成 waiting：无人值守触发器没有
    审批入口，继续跑只会留下挂起的审批和重复会话。
    """

    from app.request_context import set_request_id
    from app.services import chat_service
    from app.services.llm_config import list_enabled_providers

    providers = await list_enabled_providers(db)
    if not providers:
        return "", "没有启用的模型 provider，无法发起触发式会话", "failed"
    provider = providers[0]

    set_request_id(f"trigger-{rule.id}-{datetime.now(timezone.utc).timestamp()}")
    session = await chat_service.create_session(
        db,
        title=f"[自动] {rule.name}",
        provider=provider.provider_id,
        model=provider.default_model,
        agent_id=rule.agent_id or "default",
        user_id=f"trigger:{rule.id}",
        owner_user_id=rule.created_by,
    )
    await db.commit()

    frames = 0
    error = ""
    status = "succeeded"
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
            status = "waiting"
            break
        if frame.event == "error":
            payload = frame.data if isinstance(frame.data, dict) else {}
            error = str(payload.get("error", "回合失败"))
            status = "failed"
    if error:
        logger.info(
            "触发式会话中止 trigger_id=%s session=%s status=%s error=%s",
            rule.id,
            session.id,
            status,
            error[:120],
        )
        return session.id, error, status
    logger.info("触发式会话完成 trigger_id=%s session=%s frames=%s", rule.id, session.id, frames)
    return session.id, "", status
