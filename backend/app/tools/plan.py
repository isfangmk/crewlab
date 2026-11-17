"""规划执行工具：主代理产出可编辑计划，用户确认后由界面逐步执行。

create_plan 只建计划（draft 态）与步骤行，不自动执行——
用户在计划卡上确认「开始执行」后由 plan_service 顺序执行；
get_plan 供随时取进度快照，update_plan_steps 供按用户意见调整计划。
"""

from __future__ import annotations

import logging

from app.db import SessionLocal

logger = logging.getLogger(__name__)


async def create_plan(
    title: str,
    question: str,
    steps_json: str,
    max_steps_per_step: int = 12,
    timeout_seconds_per_step: int = 300,
    token_budget_per_step: int = 150000,
    tool_context=None,
) -> dict:
    """创建一个待确认的执行计划：产出可编辑的步骤清单，用户确认后逐步执行。

    title: 计划标题（短，概括任务）。
    question: 总目标：用户要解决的问题（一句话，会传给每个步骤）。
    steps_json: 步骤 JSON 数组，如 [{"name":"调研产品格局","instruction":"侧重国内方案","tools":["web_search"]}]，1..12 步；instruction 是本步具体要求，tools 是本步需要的工具（按声明收窄挂载，留空挂默认只读集；只需模型直接作答、无需任何工具时填 ["__none__"]）。
    max_steps_per_step: 每步最大模型调用次数（步数预算）。
    timeout_seconds_per_step: 每步墙钟超时秒数。
    token_budget_per_step: 每步 token 软限，超限中止该步。

    创建后计划处于待确认状态，用户点击「开始执行」后按步骤顺序执行；
    每步结论会回写到计划，可随时在执行进度里重试或跳过失败步骤。
    """

    from app.adk_runtime.headless import WorkerBudget
    from app.services.plan import plan_service

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    if session_id == "":
        return {"error": "缺少会话上下文，无法创建计划"}
    if title.strip() == "":
        return {"error": "title 不能为空"}
    if question.strip() == "":
        return {"error": "question 不能为空"}

    try:
        steps = plan_service.parse_steps_json(steps_json)
    except ValueError as exc:
        return {"error": str(exc)}
    budget = WorkerBudget(
        max_steps=max(1, int(max_steps_per_step)),
        timeout_seconds=max(1, int(timeout_seconds_per_step)),
        token_budget=max(0, int(token_budget_per_step)),
    )

    async with SessionLocal() as db:
        from app.models.chat import ChatSession

        session_row = await db.get(ChatSession, session_id)
        created_by = session_row.owner_user_id if session_row is not None else None
        plan = await plan_service.create_plan(
            db,
            session_id=session_id,
            title=title,
            question=question,
            steps=steps,
            budget=budget,
            created_by=created_by,
        )
        plan_uid = plan.plan_uid

    logger.info(
        "create_plan 创建计划 plan_uid=%s title=%s steps=%s", plan_uid, title, len(steps)
    )
    async with SessionLocal() as db:
        result = await plan_service.tool_result_wire(db, plan_uid)
    result["message"] = (
        "计划已创建并等待用户确认；用户确认后点击「开始执行」按步骤顺序执行。"
    )
    return result


async def get_plan(plan_uid: str) -> dict:
    """按计划标识取执行进度快照（步骤状态、结论、用量）。

    plan_uid: 计划标识（create_plan 返回或计划卡参数里）。
    """

    if not plan_uid.strip():
        return {"error": "plan_uid 不能为空"}
    from app.services.plan import plan_service

    async with SessionLocal() as db:
        wire = await plan_service.get_plan_wire(db, plan_uid.strip())
    if wire is None:
        return {"error": f"计划不存在: {plan_uid}"}
    return wire


async def update_plan_steps(plan_uid: str, steps_json: str) -> dict:
    """按用户意见调整计划步骤（排序/增删/改文案）；仅待确认状态可改。

    plan_uid: 计划标识。
    steps_json: 新的步骤 JSON 数组（整体替换），格式同 create_plan。
    """

    if not plan_uid.strip():
        return {"error": "plan_uid 不能为空"}
    from app.services.plan import plan_service

    try:
        steps = plan_service.parse_steps_json(steps_json)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as db:
        ok = await plan_service.update_steps(db, plan_uid.strip(), steps)
        if not ok:
            return {"error": "计划不存在或已开始执行，无法调整步骤"}
        result = await plan_service.tool_result_wire(db, plan_uid.strip())
    return result
