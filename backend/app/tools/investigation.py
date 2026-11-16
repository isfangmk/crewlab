"""并行子代理排查工具：主代理派发 N 路只读 worker 与结果回取。

dispatch_investigation 内部并行起子代理（复用子助理装配），
软截止后先回初步 findings 供主代理汇总，未完的路后台续跑；
get_investigation 供「汇总结论回对话」/ 重试后重新归因的回合取快照。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal

logger = logging.getLogger(__name__)


async def dispatch_investigation(
    question: str,
    workers_json: str,
    soft_deadline_seconds: int = 60,
    max_steps_per_worker: int = 12,
    timeout_seconds_per_worker: int = 300,
    token_budget_per_worker: int = 60000,
    tool_context: Any = None,
) -> dict:
    """把一个多线索问题派给 N 路只读子代理并行排查，返回带证据的结构化发现。

    question: 要排查的总问题（一句话）。
    workers_json: worker 规格 JSON 数组，如 [{"name":"指标员","target":"关联指标趋势","instruction":"侧重近24h"}]，1..8 路。
    soft_deadline_seconds: 软截止秒数；到期先返回初步结果，未完的路后台继续跑。
    max_steps_per_worker: 每路最大模型调用次数（步数预算）。
    timeout_seconds_per_worker: 每路墙钟超时秒数。
    token_budget_per_worker: 每路 token 软限，超限中止该路（默认 60000：多工具集下每轮调用约 4-5k，调研类长排查常超 3 万）。

    汇总结论时请在正文用 [n] 标记引用 findings 里的发现编号 n（如 [1]），用户可点角标定位证据。
    """

    # 服务层在函数内导入：目录注册链（catalog → 本模块）不能反向引到 worker 装配
    from app.services.investigation import dispatch_service
    from app.services.investigation.worker_runner import WorkerBudget

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    if session_id == "":
        return {"error": "缺少会话上下文，无法派发排查"}

    specs = dispatch_service.parse_workers_json(workers_json)
    budget = WorkerBudget(
        max_steps=max(0, int(max_steps_per_worker)),
        timeout_seconds=max(1, int(timeout_seconds_per_worker)),
        token_budget=max(0, int(token_budget_per_worker)),
    )
    if soft_deadline_seconds < 1 or soft_deadline_seconds > 600:
        return {"error": "soft_deadline_seconds 范围是 1..600"}

    async with SessionLocal() as db:
        from app.models.chat import ChatSession

        session_row = await db.get(ChatSession, session_id)
        created_by = session_row.owner_user_id if session_row is not None else None
        dispatch = await dispatch_service.start_dispatch(
            db,
            session_id=session_id,
            question=question,
            workers=specs,
            budget=budget,
            created_by=created_by,
        )
        dispatch_uid = dispatch.dispatch_uid

    logger.info(
        "dispatch_investigation 派发 dispatch_uid=%s workers=%s soft_deadline=%ss",
        dispatch_uid,
        [s["name"] for s in specs],
        soft_deadline_seconds,
    )
    complete = await dispatch_service.wait_dispatch(dispatch_uid, int(soft_deadline_seconds))
    async with SessionLocal() as db:
        result = await dispatch_service.tool_result_wire(db, dispatch_uid)
    if not complete:
        result["message"] = (
            f"软截止 {soft_deadline_seconds}s 已到：先基于已完成的发现汇总，"
            f"仍在跑的路有 {len(result['still_running'])} 条，"
            f"跑完后可调用 get_investigation 取最终发现再补一轮归因。"
        )
    return result


async def get_investigation(dispatch_uid: str) -> dict:
    """按 dispatch_uid 取一次并行排查的最新快照（worker 状态、发现、预算）。

    dispatch_uid: 排查标识（dispatch_investigation 返回或工具卡参数里）。
    汇总结论时请在正文用 [n] 标记引用 findings 里的发现编号 n（如 [1]），用户可点角标定位证据。
    """

    if not dispatch_uid.strip():
        return {"error": "dispatch_uid 不能为空"}
    from app.services.investigation import dispatch_service

    async with SessionLocal() as db:
        wire = await dispatch_service.get_dispatch_wire(db, dispatch_uid.strip())
    if wire is None:
        return {"error": f"排查不存在: {dispatch_uid}"}
    wire["still_running"] = [
        {"worker_uid": w["worker_uid"], "name": w["name"]}
        for w in wire["workers"]
        if w["status"] not in ("succeeded", "failed", "stopped")
    ]
    return wire
