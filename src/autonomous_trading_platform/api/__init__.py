from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.deprecation import DeprecationMiddleware, deprecated
from autonomous_trading_platform.api.envelope import (
    API_VERSION,
    ErrorDetail,
    ErrorEnvelope,
    ResponseMeta,
    SuccessEnvelope,
    error_response,
    success_response,
)
from autonomous_trading_platform.api.errors import APIError, ErrorCode
from autonomous_trading_platform.api.exception_handlers import register_exception_handlers
from autonomous_trading_platform.api.middleware import REQUEST_ID_HEADER, RequestIDMiddleware

__all__ = [
    "API_VERSION",
    "APIError",
    "DeprecationMiddleware",
    "ErrorCode",
    "ErrorDetail",
    "ErrorEnvelope",
    "REQUEST_ID_HEADER",
    "RequestIDMiddleware",
    "ResponseMeta",
    "SuccessEnvelope",
    "deprecated",
    "error_response",
    "get_request_id",
    "register_exception_handlers",
    "success_response",
]
