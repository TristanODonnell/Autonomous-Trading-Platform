from __future__ import annotations

from opentelemetry import trace

from autonomous_trading_platform.observability.enums import SpanTimespan

tracer = trace.get_tracer("autonomous_trading_platform")


def start_span(name: str, *, timespan: SpanTimespan):
    span_cm = tracer.start_as_current_span(name)
    return _SpanWithTimespan(span_cm, timespan)


class _SpanWithTimespan:
    def __init__(self, span_cm, timespan: SpanTimespan) -> None:
        self._span_cm = span_cm
        self._timespan = timespan

    def __enter__(self):
        span = self._span_cm.__enter__()
        span.set_attribute("ratp.span_timespan", self._timespan.value)
        return span

    def __exit__(self, exc_type, exc, tb):
        return self._span_cm.__exit__(exc_type, exc, tb)
