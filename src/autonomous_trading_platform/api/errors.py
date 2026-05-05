from __future__ import annotations

import enum


class ErrorCode(enum.StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_FOUND = "NOT_FOUND"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    ACTION_BLOCKED = "ACTION_BLOCKED"


class APIError(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        http_status: int = 400,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.http_status = http_status
        self.details = details
