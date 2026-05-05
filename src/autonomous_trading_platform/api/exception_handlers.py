from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from autonomous_trading_platform.api.envelope import error_response
from autonomous_trading_platform.api.errors import APIError, ErrorCode
from autonomous_trading_platform.api.middleware import REQUEST_ID_HEADER


def _request_id(request: Request) -> str:
    # Falls back to the inbound header in case middleware hasn't run (e.g. tests).
    return str(
        getattr(request.state, "request_id", None)
        or request.headers.get(REQUEST_ID_HEADER)
        or "unknown"
    )


async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    envelope = error_response(
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
        details=exc.details,
    )
    return JSONResponse(status_code=exc.http_status, content=envelope.model_dump(mode="json"))


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    envelope = error_response(
        code=ErrorCode.VALIDATION_ERROR,
        message="Request validation failed.",
        request_id=_request_id(request),
        details=exc.errors(),
    )
    return JSONResponse(status_code=422, content=envelope.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
