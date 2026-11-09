"""为每个 HTTP 请求注入 request_id。"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.request_context import set_request_id, set_session_id, set_user_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id")
        if rid is None or rid == "":
            rid = uuid.uuid4().hex
        set_request_id(rid)
        set_user_id("-")
        set_session_id("-")
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response
