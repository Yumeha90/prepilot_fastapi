from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("prepilot.access")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录方法、路径、状态码、耗时，并透传 / 生成 X-Request-ID。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            logger.exception("rid=%s 请求处理异常", request_id)
            raise
        finally:
            cost_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s -> %s (%.1f ms) rid=%s",
                request.method,
                request.url.path,
                status_code,
                cost_ms,
                request_id,
            )
        response.headers["X-Request-ID"] = request_id
        return response
