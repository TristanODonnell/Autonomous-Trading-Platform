from __future__ import annotations

import os

from autonomous_trading_platform.observability.runtime_context import get_runtime_context

_ENVIRONMENT = os.getenv("RATP_ENVIRONMENT", "unknown")


def observability_environment() -> str:
    ctx = get_runtime_context()
    if ctx is not None and ctx.environment is not None:
        return ctx.environment
    return _ENVIRONMENT


def context_strategy_id() -> str | None:
    ctx = get_runtime_context()
    return ctx.strategy_id if ctx is not None else None


def component_labels(component: str) -> dict[str, str]:
    return {
        "environment": observability_environment(),
        "component": component,
    }


def broker_labels(
    *,
    component: str,
    broker: str,
    endpoint: str,
    status: str | None = None,
) -> dict[str, str]:
    labels = {
        **component_labels(component),
        "broker": broker,
        "endpoint": endpoint,
    }
    if status is not None:
        labels["status"] = status

    strategy_id = context_strategy_id()
    if strategy_id is not None:
        labels["strategy_id"] = strategy_id

    return labels
