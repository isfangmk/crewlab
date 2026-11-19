"""为每个 HTTP 请求注入 request_id，并在统一入口记录接口耗时。"""

from __future__ import annotations

import logging
import time
import uuid
from http import HTTPStatus

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.request_context import set_request_id, set_session_id, set_user_id

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id")
        if rid is None or rid == "":
            rid = uuid.uuid4().hex
        set_request_id(rid)
        set_user_id("-")
        set_session_id("-")
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            # 所有接口在统一入口计时；异常路径 status 保留 500，照样留痕
            cost_ms = (time.perf_counter() - start) * 1000
            try:
                reason = HTTPStatus(status).phrase
            except ValueError:
                reason = ""
            logger.info(
                'HTTP Request: %s %s "%s %d %s" cost_ms=%.1f',
                request.method,
                request.url,
                f"HTTP/{request.scope.get('http_version', '1.1')}",
                status,
                reason,
                cost_ms,
            )
