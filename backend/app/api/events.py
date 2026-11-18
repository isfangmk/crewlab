"""全局通知 SSE：一个用户的所有标签页共用一个连接收状态变更通知。

替代前端按资源的 5 秒轮询：后端在消息落库/审批决定/任务图收尾/外部源同步完成/
评测跑完等状态流转处发通知（notification_bus），前端收到信号后重拉权威数据。

用 fetch + readSseFrames（不用 EventSource，带不上 Authorization）；uid 从登录态
解析，客户端无法伪造他人通知。id 字段用 Stream ID，支持 Last-Event-ID 断线续传。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth
from app.services.platform import notification_bus
from app.services.platform.auth_service import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/events")
async def stream_notifications(
    request: Request,
    user: AuthUser = Depends(require_auth),
) -> EventSourceResponse:
    """订阅当前用户的全局通知 SSE 流，支持 Last-Event-ID 断线续传。"""

    uid = user.id
    header_id = request.headers.get("last-event-id", "")
    last_id = header_id or request.query_params.get("last_id", "0-0") or "0-0"

    async def gen():
        try:
            async for stream_id, kind, payload in notification_bus.subscribe(uid, last_id=last_id):
                yield {
                    "event": kind,
                    "id": stream_id,
                    "data": json.dumps(payload, ensure_ascii=False),
                }
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - SSE 流异常记日志，不把连接抛断
            logger.exception("全局通知流异常 uid=%s", uid)

    # sep="\n" 与前端 readSseFrames 的 \n\n 分帧一致
    return EventSourceResponse(gen(), sep="\n")
