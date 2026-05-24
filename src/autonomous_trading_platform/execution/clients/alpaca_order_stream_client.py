from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

RawUpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AlpacaOrderStreamClient:
    """
    Async wrapper around alpaca-py TradingStream for order update events.

    stream_trade_updates() connects and delivers normalized event dicts to the
    callback until the task is cancelled or the connection drops.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        paper: bool,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper

    async def stream_trade_updates(self, callback: RawUpdateCallback) -> None:
        """Connect to the Alpaca order update stream and invoke callback for each event."""
        from alpaca.trading.stream import TradingStream

        stream = TradingStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=self._paper,
        )

        async def _handler(data: Any) -> None:
            received_at = datetime.now(UTC)
            try:
                payload = _extract_raw_payload(data, received_at=received_at)
                await callback(payload)
            except Exception:
                logger.exception(
                    "stream.event_handler_error",
                    extra={"event_type": str(getattr(data, "event", "unknown"))},
                )

        stream.subscribe_trade_updates(_handler)
        # _run_forever is alpaca-py's internal coroutine called by TradingStream.run().
        await stream._run_forever()


def _extract_raw_payload(data: Any, *, received_at: datetime) -> dict[str, Any]:
    """Normalize an alpaca-py TradeUpdate (or plain dict) into a canonical event dict."""
    if isinstance(data, dict):
        payload = dict(data)
    else:
        order = getattr(data, "order", None)
        payload = {
            "event": str(getattr(data, "event", "") or ""),
            "timestamp": getattr(data, "timestamp", None),
            "order": _order_to_dict(order),
            "position_qty": str(getattr(data, "position_qty", "") or ""),
            "price": str(getattr(data, "price", "") or ""),
            "qty": str(getattr(data, "qty", "") or ""),
        }
    payload["stream_received_at"] = received_at.isoformat()
    return payload


def _order_to_dict(order: Any) -> dict[str, Any]:
    if order is None:
        return {}
    if isinstance(order, dict):
        return order
    try:
        result = order.dict()
        return result  # type: ignore[no-any-return]
    except AttributeError:
        pass
    try:
        return {k: v for k, v in vars(order).items() if not k.startswith("_")}
    except TypeError:
        return {}
