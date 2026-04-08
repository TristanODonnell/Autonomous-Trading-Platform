from __future__ import annotations

from opentelemetry import trace

tracer = trace.get_tracer("autonomous_trading_platform")


def start_span(name: str):
    return tracer.start_as_current_span(name)
