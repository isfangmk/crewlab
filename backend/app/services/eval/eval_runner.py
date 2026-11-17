"""评测执行器：逐用例跑真实助理会话，按确定性断言判分。

运行放在后台任务里，发起接口立即返回 run_uid；每跑完一条用例就落库并提交，
前端轮询能看到进度。评测会话保留在会话列表，trace 可从结果直接跳转。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.eval import (
    EvalCaseResult,
    EvalJudgeConfig,
    EvalJudgeScore,
    EvalRun,
)
from app.request_context import set_request_id
from app.services.chat import chat_service
from app.services.eval import eval_judge_service, eval_service
from app.services.observability import observation_service
from app.services.observability.audit_service import write_audit
from app.services.platform import notification_bus

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[None]] = set()


def start_run(run_uid: str) -> None:
    """登记后台任务并保留强引用，避免事件循环回收。"""

    task = asyncio.create_task(_run_detached(run_uid))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _run_detached(run_uid: str) -> None:
    try:
        async with SessionLocal() as db:
            run = await eval_service.get_run(db, run_uid)
            snapshots = json.loads(run.cases_snapshot_json)
            if not isinstance(snapshots, list):
                raise TypeError("cases_snapshot_json 必须是 JSON 数组")
            judge_config = await db.scalar(
                select(EvalJudgeConfig).where(EvalJudgeConfig.dataset_id == run.dataset_id)
            )
            for index, snapshot in enumerate(snapshots):
                result = await _run_case(db, run, snapshot, index)
                scores: list[EvalJudgeScore] = []
                # 对抗用例的安全断言在 _run_case 已判；这里补 LLM 评分
                if (
                    judge_config is not None
                    and judge_config.enabled
                    and result.status != eval_service.CASE_ERROR
                ):
                    scores = await eval_judge_service.judge_result(
                        db,
                        result,
                        judge_config,
                        provider_id=run.provider,
                        model=run.model,
                    )
                    _apply_judge(result, scores, judge_config)
                db.add(result)
                # 先落结果再落评分：评分外键指向结果，显式保证插入顺序
                await db.flush()
                for score in scores:
                    db.add(score)
                if result.status == eval_service.CASE_PASSED:
                    run.passed_cases += 1
                else:
                    run.failed_cases += 1
                await db.commit()
            eval_service.finish_run_now(run, eval_service.RUN_COMPLETED)
            await db.commit()
            await notification_bus.publish(
                run.created_by,
                kind="eval_run_done",
                payload={"run_uid": run.run_uid, "status": eval_service.RUN_COMPLETED},
            )
            await write_audit(
                actor_user_id=run.created_by,
                action="eval.run_finish",
                resource_type="eval_run",
                resource_id=run.run_uid,
                detail={
                    "dataset_id": run.dataset_id,
                    "dataset_name": run.dataset_name,
                    "agent_id": run.agent_id,
                    "model": run.model,
                    "total_cases": run.total_cases,
                    "passed_cases": run.passed_cases,
                    "failed_cases": run.failed_cases,
                },
            )
            logger.info(
                "评测运行完成 run_uid=%s dataset=%s passed=%s/%s",
                run.run_uid,
                run.dataset_name,
                run.passed_cases,
                run.total_cases,
            )
    except Exception as exc:
        logger.exception("评测运行失败 run_uid=%s", run_uid)
        await _fail_run(run_uid, f"{type(exc).__name__}: {exc}")


async def _fail_run(run_uid: str, error: str) -> None:
    """执行链路整体异常：把运行显式标失败，避免前端永远转圈。"""

    async with SessionLocal() as db:
        run = await eval_service.get_run(db, run_uid)
        eval_service.finish_run_now(run, eval_service.RUN_FAILED, error)
        await db.commit()
        await notification_bus.publish(
            run.created_by,
            kind="eval_run_done",
            payload={"run_uid": run.run_uid, "status": eval_service.RUN_FAILED},
        )
        await write_audit(
            actor_user_id=run.created_by,
            action="eval.run_finish",
            resource_type="eval_run",
            resource_id=run.run_uid,
            detail={
                "dataset_id": run.dataset_id,
                "status": eval_service.RUN_FAILED,
                "error": error[:500],
            },
        )


def _tool_names(tool_calls: list[Any]) -> list[str]:
    names: list[str] = []
    for row in tool_calls:
        if row.name not in names:
            names.append(row.name)
    return names


def _contains(text: str, needle: str) -> bool:
    return needle.casefold() in text.casefold()


def judge(
    snapshot: dict[str, Any],
    *,
    output: str,
    tool_names: list[str],
    duration_ms: int | None,
    total_tokens: int | None,
    turn_status: str,
    error: str,
) -> list[str]:
    """确定性判分：返回失败原因；空列表 = 通过。"""

    reasons: list[str] = []
    if error != "":
        reasons.append(error)
    if turn_status != "" and turn_status != "ok":
        reasons.append(f"回合状态异常: {turn_status}")
    if output.strip() == "":
        reasons.append("模型输出为空")
    for keyword in snapshot["expected_keywords"]:
        if not _contains(output, keyword):
            reasons.append(f"缺少期望关键词: {keyword}")
    for keyword in snapshot["forbidden_keywords"]:
        if _contains(output, keyword):
            reasons.append(f"出现禁止关键词: {keyword}")
    called = {name.casefold() for name in tool_names}
    for name in snapshot["expected_tools"]:
        if name.casefold() not in called:
            reasons.append(f"未调用期望工具: {name}")
    for name in snapshot["forbidden_tools"]:
        if name.casefold() in called:
            reasons.append(f"调用了禁止工具: {name}")
    max_duration = snapshot["max_duration_ms"]
    if max_duration is not None:
        if duration_ms is None:
            reasons.append("缺少耗时数据，无法校验 max_duration_ms")
        elif duration_ms > max_duration:
            reasons.append(f"耗时 {duration_ms}ms 超过上限 {max_duration}ms")
    max_tokens = snapshot["max_total_tokens"]
    if max_tokens is not None:
        if total_tokens is None:
            reasons.append("缺少 token 用量，无法校验 max_total_tokens")
        elif total_tokens > max_tokens:
            reasons.append(f"tokens {total_tokens} 超过上限 {max_tokens}")
    # 对抗用例的安全断言：输出命中泄漏/合规词即判失败
    for keyword in snapshot.get("safety_keywords") or []:
        if _contains(output, keyword):
            reasons.append(f"安全断言命中: {keyword}")
    return reasons


def _apply_judge(
    result: EvalCaseResult,
    scores: list[EvalJudgeScore],
    config: EvalJudgeConfig,
) -> None:
    """LLM 评分判定：平均分低于阈值时把结果翻成失败并记原因。

    只有 parse_error（评分链路本身失败）时不改判定，留给人工复核。
    """

    dims = [s for s in scores if s.dimension != "parse"]
    if not dims:
        return
    avg = sum(s.score for s in dims) / len(dims)
    if avg >= config.pass_score:
        return
    reasons = json.loads(result.failure_reasons_json or "[]")
    reasons.append(f"LLM 评分 {avg:.1f} 低于阈值 {config.pass_score:.1f}")
    result.failure_reasons_json = json.dumps(reasons, ensure_ascii=False)
    result.status = eval_service.CASE_FAILED


async def _run_case(
    db: AsyncSession,
    run: EvalRun,
    snapshot: dict[str, Any],
    index: int,
) -> EvalCaseResult:
    """跑一条用例：真实会话 → 观测 → 判分 → 结果快照。"""

    started = time.monotonic()
    case_name = str(snapshot.get("name", ""))
    input_text = str(snapshot.get("input", ""))
    result_id = uuid4().hex
    session_id = ""
    trace_id = ""
    output_parts: list[str] = []
    approval_error = ""
    stream_error = ""
    usage: dict[str, int | None] | None = None

    set_request_id(f"eval-{run.run_uid}-{index + 1}")
    try:
        session = await chat_service.create_session(
            db,
            title=f"[评测] {run.dataset_name} · {case_name}"[:200],
            provider=run.provider,
            model=run.model,
            agent_id=run.agent_id,
            user_id=f"eval:{run.run_uid}",
            owner_user_id=run.created_by,
        )
        session_id = session.id
        async for frame in chat_service.stream_session_message(
            db,
            session_id=session_id,
            content=input_text,
            provider=None,
            model=None,
            client_message_id=None,
            thinking_enabled=False,
            web_search_enabled=False,
            attachment_ids=[],
            locale=None,
            mentions=None,
        ):
            if frame.event == "assistant_delta":
                delta = frame.data.get("delta")
                if isinstance(delta, str):
                    output_parts.append(delta)
            elif frame.event == "approval_pending":
                approval_error = "回合需要人工审批；评测不支持审批，请换用只读工具的助理"
                break
            elif frame.event == "error":
                stream_error = str(frame.data.get("error") or "回合失败")
            elif frame.event == "done":
                trace_id = str(frame.data.get("trace_id") or "")
                raw_usage = frame.data.get("usage")
                if isinstance(raw_usage, dict):
                    usage = {
                        "prompt_tokens": raw_usage.get("prompt_tokens"),
                        "completion_tokens": raw_usage.get("completion_tokens"),
                        "total_tokens": raw_usage.get("total_tokens"),
                    }
    except Exception as exc:
        stream_error = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "评测用例执行异常 run_uid=%s case=%s", run.run_uid, case_name
        )

    output = "".join(output_parts)
    tool_names: list[str] = []
    duration_ms: int | None = int((time.monotonic() - started) * 1000)
    total_tokens: int | None = None
    turn_status = ""
    turn_error = ""
    if trace_id != "":
        try:
            turn = await observation_service.get_turn(db, trace_id)
            turn_status = turn.status
            turn_error = turn.error_message or ""
            if turn.duration_ms is not None:
                duration_ms = turn.duration_ms
            total_tokens = turn.total_tokens
            tool_names = _tool_names(
                await observation_service.list_tool_calls(db, trace_id)
            )
        except LookupError:
            logger.warning("评测结果找不到观测回合 trace_id=%s", trace_id)
    if total_tokens is None and usage is not None:
        raw_total = usage.get("total_tokens")
        total_tokens = raw_total if isinstance(raw_total, int) else None

    error = approval_error or stream_error or turn_error
    reasons = judge(
        snapshot,
        output=output,
        tool_names=tool_names,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        turn_status=turn_status,
        error=error,
    )
    if approval_error != "" or stream_error != "" or turn_status == "error":
        status = eval_service.CASE_ERROR
    elif len(reasons) == 0:
        status = eval_service.CASE_PASSED
    else:
        status = eval_service.CASE_FAILED
    if status == eval_service.CASE_PASSED:
        reasons = []

    logger.info(
        "评测用例完成 run_uid=%s case=%s status=%s trace=%s tools=%s",
        run.run_uid,
        case_name,
        status,
        trace_id,
        tool_names,
    )
    return EvalCaseResult(
        id=result_id,
        run_uid=run.run_uid,
        case_id=snapshot.get("case_id"),
        case_name=case_name,
        input=input_text,
        status=status,
        output=output,
        failure_reasons_json=json.dumps(reasons, ensure_ascii=False),
        tool_names_json=json.dumps(tool_names, ensure_ascii=False),
        trace_id=trace_id,
        session_id=session_id,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        created_at=datetime.now(timezone.utc),
    )
