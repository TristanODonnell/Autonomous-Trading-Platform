from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        # request_id and user_id are set by RequestIDMiddleware and JWTAuthMiddleware
        # respectively; fall back gracefully if either hasn't run (e.g. public routes)
        request_id: str = getattr(request.state, "request_id", None) or "unknown"
        user_id: str | None = getattr(request.state, "user_id", None)

        logger.info(
            "api_request",
            extra={
                # EPIC-1 / Story 4 structured log fields
                "request_id": request_id,
                "user_id": user_id,
                "method": request.method,
                "endpoint": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )

        return response
