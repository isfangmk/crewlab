"""并行子代理排查工具：主代理派发 N 路只读 worker 与快照回取。

dispatch_investigation 派发后立即返回运行句柄（编排等全部路收口，
结论由自动汇总写回对话）；get_investigation 供重试后重新归因的回合取快照。
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal

logger = logging.getLogger(__name__)


async def dispatch_investigation(
    question: str,
    workers_json: str,
    max_steps_per_worker: int = 12,
    timeout_seconds_per_worker: int = 300,
    token_budget_per_worker: int = 150000,
    tool_context: Any = None,
) -> dict:
    """把问题拆给 N 路只读子代理并行排查，收口后自动汇总进对话。

    question: 要排查的总问题（一句话）。
    workers_json: worker 规格 JSON 数组，如 [{"name":"指标员","target":"关联指标趋势","instruction":"侧重近24h","tools":["query_knowledge"]}]，1..8 路；tools 可选，是本路需要的工具（按声明收窄挂载，留空挂默认只读集；只需模型直接作答、无需任何工具时填 ["__none__"]）。
    max_steps_per_worker: 每路最大模型调用次数（步数预算）。
    timeout_seconds_per_worker: 每路墙钟超时秒数。
    token_budget_per_worker: 每路 token 软限，超限中止该路（默认 150000：多工具集下每轮调用约 4-5k，检索返回会随上下文累积放大）。

    返回启动句柄（路名单），不返回结论；全部路收口后编排自动把汇总结论
    写进对话流。要取某次排查的快照（状态/发现/证据）用 get_investigation。
    """

    # 服务层在函数内导入：目录注册链（catalog → 本模块）不能反向引到 worker 装配
    from app.adk_runtime.headless import WorkerBudget
    from app.services.investigation import dispatch_service

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
        "dispatch_investigation 派发 dispatch_uid=%s workers=%s",
        dispatch_uid,
        [s["name"] for s in specs],
    )
    async with SessionLocal() as db:
        handle = await dispatch_service.dispatch_handle_wire(db, dispatch_uid)
    return handle


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
