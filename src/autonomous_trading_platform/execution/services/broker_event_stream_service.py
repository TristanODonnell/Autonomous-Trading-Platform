from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from autonomous_trading_platform.observability.logging import get_logger

logger = get_logger(__name__)

_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_BACKOFF_MULTIPLIER = 2.0

RawUpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]


class BrokerEventStreamService:
    """
    Manages the broker order update stream lifecycle with exponential backoff reconnects.

    Architecture
    ------------
    - Websocket stream (this service) = primary near-real-time fill/status source.
    - Polling reconciliation = secondary recovery/backstop path.
    - Events from both paths converge in BrokerStreamFillProcessor for idempotent
      delta-qty fill extraction, so fills cannot be double-counted regardless of source.

    Usage
    -----
    Construct with a stream_client and an on_event async callback, then await start().
    Call stop() from another task/thread to request graceful shutdown.
    """

    def __init__(
        self,
        *,
        stream_client: Any,
        on_event: RawUpdateCallback,
        initial_backoff_seconds: float = _INITIAL_BACKOFF,
        max_backoff_seconds: float = _MAX_BACKOFF,
    ) -> None:
        self._stream_client = stream_client
        self._on_event = on_event
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._stop_event: asyncio.Event | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """
        Start the stream with reconnect logic.

        Returns when stop() is called or the task is cancelled.
        """
        if self._running:
            logger.warning("stream_service.already_running")
            return

        self._stop_event = asyncio.Event()
        self._running = True

        try:
            await self._run_with_reconnect()
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the stream to stop gracefully after the current connection drops."""
        if self._stop_event is not None:
            self._stop_event.set()

    async def _run_with_reconnect(self) -> None:
        assert self._stop_event is not None

        backoff = self._initial_backoff
        attempt = 0

        while not self._stop_event.is_set():
            attempt += 1
            connected_at = datetime.now(UTC)

            logger.info(
                "stream_service.connecting",
                extra={
                    "attempt": attempt,
                    "backoff_before_connect_seconds": backoff if attempt > 1 else 0,
                },
            )

            try:
                await self._stream_client.stream_trade_updates(self._handle_event)
                # Clean exit — stream closed without error; reset backoff.
                logger.info("stream_service.stream_closed_normally")
                backoff = self._initial_backoff
                attempt = 0
                # Yield to let other coroutines run before reconnecting.
                await asyncio.sleep(0)

            except asyncio.CancelledError:
                logger.info("stream_service.cancelled")
                raise

            except Exception as exc:
                if self._stop_event.is_set():
                    break

                duration = (datetime.now(UTC) - connected_at).total_seconds()
                logger.warning(
                    "stream_service.connection_error",
                    extra={
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "connected_duration_seconds": round(duration, 3),
                        "reconnect_backoff_seconds": backoff,
                    },
                )

                # Wait for backoff or stop signal, whichever comes first.
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break  # stop_event was set during backoff
                except TimeoutError:
                    pass

                backoff = min(backoff * _BACKOFF_MULTIPLIER, self._max_backoff)

        logger.info("stream_service.stopped")

    async def _handle_event(self, event_payload: dict[str, Any]) -> None:
        """Receive a raw stream event and forward to the on_event callback."""
        event_type = str(event_payload.get("event", "unknown"))
        broker_order_id = str((event_payload.get("order") or {}).get("id", ""))

        logger.debug(
            "stream_service.event_received",
            extra={
                "event_type": event_type,
                "broker_order_id": broker_order_id,
                "stream_received_at": event_payload.get("stream_received_at"),
            },
        )

        try:
            await self._on_event(event_payload)
        except Exception:
            logger.exception(
                "stream_service.on_event_error",
                extra={
                    "event_type": event_type,
                    "broker_order_id": broker_order_id,
                },
            )
