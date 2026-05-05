from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

API_VERSION = "v1"


class ResponseMeta(BaseModel):
    request_id: str
    timestamp: datetime
    version: str


class SuccessEnvelope(BaseModel, Generic[T]):
    data: T
    meta: ResponseMeta
    error: None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None


class ErrorEnvelope(BaseModel):
    data: None = None
    error: ErrorDetail
    meta: ResponseMeta


def success_response(data: T, request_id: str) -> SuccessEnvelope[T]:
    return SuccessEnvelope(
        data=data,
        meta=ResponseMeta(
            request_id=request_id,
            timestamp=datetime.now(UTC),
            version=API_VERSION,
        ),
    )


def error_response(
    code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
) -> ErrorEnvelope:
    return ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details),
        meta=ResponseMeta(
            request_id=request_id,
            timestamp=datetime.now(UTC),
            version=API_VERSION,
        ),
    )
