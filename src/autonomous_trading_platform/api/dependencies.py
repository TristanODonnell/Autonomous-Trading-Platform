from __future__ import annotations

from fastapi import HTTPException, Request, status


def get_request_id(request: Request) -> str:
    return request.state.request_id  # type: ignore[no-any-return]


def require_operator_or_admin(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if role not in {"operator", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator or admin role required.",
        )

    return str(getattr(request.state, "user_id", None) or "unknown")


def require_risk_manager_or_admin(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if role not in {"risk_manager", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Risk manager or admin role required.",
        )

    return str(getattr(request.state, "user_id", None) or "unknown")


def require_admin(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )

    return str(getattr(request.state, "user_id", None) or "unknown")
