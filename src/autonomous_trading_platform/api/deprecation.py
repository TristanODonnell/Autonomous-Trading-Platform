from __future__ import annotations

from datetime import date

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_SUNSET_ATTR = "_deprecated_sunset"


def deprecated(sunset: date):
    """Mark a route endpoint as deprecated. DeprecationMiddleware reads this to set response headers."""

    def decorator(func):
        setattr(func, _SUNSET_ATTR, sunset.isoformat())
        return func

    return decorator


class DeprecationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        route = request.scope.get("route")
        if route is not None:
            sunset = getattr(getattr(route, "endpoint", None), _SUNSET_ATTR, None)
            if sunset:
                response.headers["Deprecation"] = "true"
                response.headers["Sunset"] = sunset
        return response
