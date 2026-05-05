from __future__ import annotations

import os
from collections.abc import Sequence

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

VALID_ROLES = {"operator", "researcher", "risk_manager", "admin"}

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")

# Routes that don't require authentication
PUBLIC_PATHS: Sequence[str] = [
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
]


class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if any(request.url.path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Authorization header"},
            )

        token = auth_header.removeprefix("Bearer ")

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token has expired"},
            )
        except jwt.InvalidTokenError as exc:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Invalid token: {exc}"},
            )

        role: str | None = payload.get("role")
        if not role or role not in VALID_ROLES:
            return JSONResponse(
                status_code=403,
                content={"detail": f"Token must carry a valid role claim; got {role!r}"},
            )

        request.state.user_id = payload.get("sub")
        request.state.role = role

        return await call_next(request)
