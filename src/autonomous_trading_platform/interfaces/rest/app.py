from __future__ import annotations

from fastapi import FastAPI

from autonomous_trading_platform.api import (
    RequestIDMiddleware,
    register_exception_handlers,
)
from autonomous_trading_platform.api.auth_middleware import JWTAuthMiddleware
from autonomous_trading_platform.api.deprecation import DeprecationMiddleware
from autonomous_trading_platform.api.logging_middleware import RequestLoggingMiddleware
from autonomous_trading_platform.interfaces.rest.routes.activity_routes import (
    router as activity_router,
)
from autonomous_trading_platform.interfaces.rest.routes.control_routes import (
    router as control_router,
)
from autonomous_trading_platform.interfaces.rest.routes.metadata_routes import (
    router as metadata_router,
)
from autonomous_trading_platform.interfaces.rest.routes.operations_routes import (
    router as operations_router,
)
from autonomous_trading_platform.interfaces.rest.routes.portfolio_routes import (
    router as portfolio_router,
)
from autonomous_trading_platform.interfaces.rest.routes.strategies_routes import (
    router as strategies_router,
)
from autonomous_trading_platform.interfaces.rest.routes.system_routes import (
    router as system_router,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Autonomous Trading Platform", version="1.0.0")

    # last add_middleware = outermost layer = first to run on each request
    # order: RequestID → Logging → JWT → Deprecation → route handler
    app.add_middleware(DeprecationMiddleware)
    app.add_middleware(JWTAuthMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    app.include_router(metadata_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(portfolio_router, prefix="/api/v1")
    app.include_router(strategies_router, prefix="/api/v1")
    app.include_router(activity_router, prefix="/api/v1")
    app.include_router(control_router, prefix="/api/v1")
    app.include_router(operations_router, prefix="/api/v1")

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    return app
