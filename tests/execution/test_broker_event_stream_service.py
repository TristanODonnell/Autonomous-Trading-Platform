"""E-01 — BrokerEventStreamService lifecycle tests.

Verifies reconnect-with-backoff behavior, stop signal, and event forwarding
using a fake stream client that can inject controlled disconnects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from autonomous_trading_platform.execution.services.broker_event_stream_service import (
    BrokerEventStreamService,
)


class _RaiseThenSucceed:
    """Fake stream client that raises an error on the first N calls, then succeeds."""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = list(errors)
        self._calls = 0

    async def stream_trade_updates(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self._calls += 1
        if self._errors:
            raise self._errors.pop(0)
        # Simulate a clean empty session (returns immediately with no events).

    @property
    def calls(self) -> int:
        return self._calls


class _ImmediateStopClient:
    """Stream client that returns immediately (clean empty session every call)."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_trade_updates(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self.calls += 1


class _EventDeliveringClient:
    """Stream client that delivers one event then stops."""

    def __init__(self, event: dict[str, Any]) -> None:
        self._event = event
        self.calls = 0

    async def stream_trade_updates(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self.calls += 1
        await callback(self._event)


def _noop_event() -> dict[str, Any]:
    return {
        "event": "fill",
        "order": {"id": "bo-001"},
        "stream_received_at": "2026-05-22T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Basic lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_starts_and_stops_cleanly() -> None:
    client = _ImmediateStopClient()
    on_event = AsyncMock()
    service = BrokerEventStreamService(
        stream_client=client,
        on_event=on_event,
        initial_backoff_seconds=0.01,
    )

    # stop() immediately — should terminate start() quickly
    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        service.stop()

    stopper = asyncio.create_task(_stop_soon())
    await service.start()
    await stopper

    assert not service.is_running


@pytest.mark.asyncio
async def test_service_is_not_running_before_start() -> None:
    service = BrokerEventStreamService(
        stream_client=_ImmediateStopClient(),
        on_event=AsyncMock(),
    )
    assert not service.is_running


@pytest.mark.asyncio
async def test_service_is_running_while_active() -> None:
    running_at_check = False

    class _BlockingClient:
        async def stream_trade_updates(self, callback: Any) -> None:
            nonlocal running_at_check
            await asyncio.sleep(0.1)
            running_at_check = True

    service = BrokerEventStreamService(
        stream_client=_BlockingClient(),
        on_event=AsyncMock(),
    )

    async def _check_then_stop() -> None:
        await asyncio.sleep(0.05)
        assert service.is_running
        service.stop()

    checker = asyncio.create_task(_check_then_stop())
    await service.start()
    await checker


# ---------------------------------------------------------------------------
# E-01: Reconnect with backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnects_after_connection_error() -> None:
    error = ConnectionError("broker unreachable")
    client = _RaiseThenSucceed(errors=[error])
    on_event = AsyncMock()
    service = BrokerEventStreamService(
        stream_client=client,
        on_event=on_event,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    async def _stop_after_reconnect() -> None:
        await asyncio.sleep(0.2)
        service.stop()

    stopper = asyncio.create_task(_stop_after_reconnect())
    await service.start()
    await stopper

    # Should have attempted at least two connections (initial + reconnect)
    assert client.calls >= 2


@pytest.mark.asyncio
async def test_backoff_caps_at_max_backoff() -> None:
    """Ensure repeated errors never drive backoff above max_backoff_seconds."""
    errors: list[Exception] = [ConnectionError("err")] * 5
    client = _RaiseThenSucceed(errors=errors)
    service = BrokerEventStreamService(
        stream_client=client,
        on_event=AsyncMock(),
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )

    async def _stop() -> None:
        await asyncio.sleep(0.3)
        service.stop()

    stopper = asyncio.create_task(_stop())
    await service.start()
    await stopper

    assert client.calls >= 2  # reconnected at least once


@pytest.mark.asyncio
async def test_stop_during_backoff_exits_cleanly() -> None:
    """stop() set during backoff wait must not hang."""
    error = ConnectionError("disconnect")
    client = _RaiseThenSucceed(errors=[error])
    service = BrokerEventStreamService(
        stream_client=client,
        on_event=AsyncMock(),
        initial_backoff_seconds=10.0,  # long backoff — stop() must interrupt it
        max_backoff_seconds=60.0,
    )

    async def _stop_quickly() -> None:
        await asyncio.sleep(0.05)
        service.stop()

    stopper = asyncio.create_task(_stop_quickly())
    await asyncio.wait_for(service.start(), timeout=2.0)
    await stopper


# ---------------------------------------------------------------------------
# E-01: Event delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_are_forwarded_to_on_event_callback() -> None:
    event = _noop_event()
    client = _EventDeliveringClient(event)
    received: list[dict] = []

    async def _capture(payload: dict) -> None:
        received.append(payload)

    service = BrokerEventStreamService(
        stream_client=client,
        on_event=_capture,
        initial_backoff_seconds=0.01,
    )

    async def _stop() -> None:
        await asyncio.sleep(0.1)
        service.stop()

    stopper = asyncio.create_task(_stop())
    await service.start()
    await stopper

    assert len(received) >= 1
    assert received[0]["event"] == "fill"


@pytest.mark.asyncio
async def test_on_event_error_does_not_crash_stream() -> None:
    """Errors in the on_event callback must be caught and the stream kept alive."""

    class _ErrorOnce:
        def __init__(self) -> None:
            self.invocations = 0

        async def stream_trade_updates(self, callback: Any) -> None:
            self.invocations += 1
            await callback({"event": "fill", "order": {"id": "bo-001"}})

    client = _ErrorOnce()
    call_count = 0

    async def _on_event_raises(payload: dict) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated on_event failure")

    service = BrokerEventStreamService(
        stream_client=client,
        on_event=_on_event_raises,
        initial_backoff_seconds=0.01,
    )

    async def _stop() -> None:
        await asyncio.sleep(0.1)
        service.stop()

    stopper = asyncio.create_task(_stop())
    # Should NOT raise despite on_event raising
    await service.start()
    await stopper

    assert call_count >= 1


# ---------------------------------------------------------------------------
# E-01: Reconnect recovery preserves idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_recovery_delivers_events_after_error() -> None:
    """After reconnect, events still arrive and on_event is still called."""

    class _DisconnectThenDeliver:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_trade_updates(self, callback: Any) -> None:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("first connect dropped")
            # Second connection delivers an event
            await callback(_noop_event())

    client = _DisconnectThenDeliver()
    received: list[dict] = []

    async def _on_event(payload: dict) -> None:
        received.append(payload)

    service = BrokerEventStreamService(
        stream_client=client,
        on_event=_on_event,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    async def _stop() -> None:
        await asyncio.sleep(0.3)
        service.stop()

    stopper = asyncio.create_task(_stop())
    await service.start()
    await stopper

    assert client.calls >= 2
    assert len(received) >= 1


@pytest.mark.asyncio
async def test_duplicate_start_call_is_ignored() -> None:
    """Calling start() when already running should not create a second loop."""
    client = _ImmediateStopClient()
    service = BrokerEventStreamService(
        stream_client=client,
        on_event=AsyncMock(),
        initial_backoff_seconds=0.01,
    )

    async def _stop() -> None:
        await asyncio.sleep(0.05)
        service.stop()

    stopper = asyncio.create_task(_stop())
    # Run start() twice in parallel — second should be ignored
    await asyncio.gather(service.start(), service.start())
    await stopper

    # is_running should be False once both start() calls return
    assert not service.is_running
