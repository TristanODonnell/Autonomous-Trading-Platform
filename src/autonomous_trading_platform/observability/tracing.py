from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.runtime_context import get_runtime_context

tracer = trace.get_tracer("autonomous_trading_platform")


def start_span(name: str, *, timespan: SpanTimespan, **attrs: object):
    span_cm = tracer.start_as_current_span(name)
    return _SpanWithTimespan(span_cm, timespan, attrs)


class _SpanWithTimespan:
    def __init__(self, span_cm, timespan: SpanTimespan, extra_attrs: dict) -> None:
        self._span_cm = span_cm
        self._timespan = timespan
        self._extra_attrs = extra_attrs

    def __enter__(self):
        span = self._span_cm.__enter__()
        span.set_attribute("ratp.span_timespan", self._timespan.value)

        ctx = get_runtime_context()
        if ctx is not None:
            if ctx.correlation_id:
                span.set_attribute("ratp.correlation_id", ctx.correlation_id)
            if ctx.run_id:
                span.set_attribute("ratp.run_id", ctx.run_id)
            if ctx.environment:
                span.set_attribute("ratp.environment", ctx.environment)
            if ctx.job_run_id:
                span.set_attribute("ratp.job_run_id", ctx.job_run_id)
            if ctx.job_name:
                span.set_attribute("ratp.job_name", ctx.job_name)
            if ctx.strategy_id:
                span.set_attribute("ratp.strategy_id", ctx.strategy_id)
            if ctx.dataset_version:
                span.set_attribute("ratp.dataset_version", ctx.dataset_version)
            if ctx.universe_version:
                span.set_attribute("ratp.universe_version", ctx.universe_version)

        for key, value in self._extra_attrs.items():
            if value is not None:
                span.set_attribute(f"ratp.{key}", str(value))
                if key == "broker_endpoint":
                    span.set_attribute("ratp.broker.endpoint", str(value))
                elif key == "broker_status":
                    span.set_attribute("ratp.broker.status", str(value))

        return span

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
        return self._span_cm.__exit__(exc_type, exc, tb)
