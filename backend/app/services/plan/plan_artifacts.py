"""计划执行中 run_python 产出物的统一落库与消息挂载。

步骤与收尾汇总都会跑 headless 回合，run_python 的图表产出走进程内暂存
（remember_pending），由这里在 on_tool_response 里取走并落 artifacts 表；
再合成 tool_call 挂到消息上供前端内联渲染。一处收口，两条路径共用。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat import artifact_service

logger = logging.getLogger(__name__)


async def persist_tool_response(
    db: AsyncSession,
    *,
    session_id: str,
    message_id: str,
    call_id: str,
    name: str,
    response: Any,
) -> list[Any]:
    """run_python 返回到达时把暂存产出物落库；非 run_python/无产出物返回空。"""

    if name != "run_python" or not isinstance(response, dict):
        return []
    try:
        rows = await artifact_service.save_from_tool_result(
            db,
            session_id=session_id,
            tool_call_id=call_id,
            tool_name=name,
            message_id=message_id,
        )
        if rows:
            logger.info(
                "计划产出物落库 session_id=%s message_id=%s call_id=%s count=%s",
                session_id,
                message_id,
                call_id,
                len(rows),
            )
        return rows
    except Exception:  # noqa: BLE001 - 产出物落库失败不拖垮执行/汇总正文
        logger.warning("计划产出物落库失败 call_id=%s", call_id, exc_info=True)
        return []


def synthetic_tool_call(call_id: str, rows: list[Any]) -> dict[str, Any]:
    """合成 run_python 工具卡：让产出物在消息正文下方内联渲染。"""

    return {
        "id": f"plan-py-{call_id}",
        "name": "run_python",
        "status": "success",
        "arguments": {},
        "result": artifact_service.tool_result_update(rows),
    }
