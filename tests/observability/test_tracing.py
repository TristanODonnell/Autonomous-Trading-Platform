from __future__ import annotations

from autonomous_trading_platform.observability import tracing
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.runtime_context import runtime_context


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        return None

    def set_status(self, *args) -> None:
        return None


class FakeSpanContextManager:
    def __init__(self, span: FakeSpan) -> None:
        self.span = span

    def __enter__(self) -> FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeTracer:
    def __init__(self, span: FakeSpan) -> None:
        self.span = span

    def start_as_current_span(self, name: str) -> FakeSpanContextManager:
        return FakeSpanContextManager(self.span)


def test_start_span_applies_runtime_context_and_broker_attributes(monkeypatch) -> None:
    span = FakeSpan()
    monkeypatch.setattr(tracing, "tracer", FakeTracer(span))

    with (
        runtime_context(
            correlation_id="corr-1",
            run_id="run-1",
            job_run_id="job-run-1",
            job_name="job",
            environment="test",
            strategy_id="strategy-1",
        ),
        tracing.start_span(
            "broker_call",
            timespan=SpanTimespan.REQUEST,
            broker_endpoint="orders.submit",
            broker_status="200",
        ),
    ):
        pass

    assert span.attributes["ratp.environment"] == "test"
    assert span.attributes["ratp.job_run_id"] == "job-run-1"
    assert span.attributes["ratp.correlation_id"] == "corr-1"
    assert span.attributes["ratp.broker.endpoint"] == "orders.submit"
    assert span.attributes["ratp.broker.status"] == "200"
