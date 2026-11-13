"""审计日志写入。

审计走自己的会话：写一条立刻提交，不掺进调用方的事务。
之前复用请求级会话并顺手 commit，会把服务层还没收尾的改动一起提交，
事务边界就没了；审计本身要求「操作发生即落库」，独立会话正好满足。
"""

from __future__ import annotations

import json
import logging

from app.db import SessionLocal
from app.models_orm.audit import AuditLog
from app.request_context import get_request_id

logger = logging.getLogger(__name__)


async def write_audit(
    *,
    actor_user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str = "",
    detail: dict | None = None,
) -> None:
    if action == "":
        raise ValueError("action 不能为空")
    if resource_type == "":
        raise ValueError("resource_type 不能为空")
    payload = detail if detail is not None else {}
    row = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail_json=json.dumps(payload, ensure_ascii=False),
        request_id=get_request_id(),
    )
    async with SessionLocal() as db:
        db.add(row)
        await db.commit()
    logger.info(
        "审计写入 action=%s resource=%s/%s actor=%s",
        action,
        resource_type,
        resource_id,
        actor_user_id,
    )
