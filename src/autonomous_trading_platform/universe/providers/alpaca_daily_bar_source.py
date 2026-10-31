"""AlpacaDailyBarSource — daily bars for candidate scoring, fetched per run (Finding 16).

The live monthly universe rotation on the trading box scores a ~500-name pool, but
that box only ingests five-minute bars for the names it currently trades. Reading
local storage therefore rejected every pool name (``no_market_data`` for the rest of
the pool, ``insufficient_history`` for the traded names seen through a one-session
dataset version). This source fetches ``TimeFrame.Day`` bars from Alpaca for the pool
in batches, the same data the pool-refresh screener already ranks on, so the scorer
sees full history for every name without a new ingestion pipeline.

Replays and backtests keep reading their pinned Parquet dataset version; this source
is only wired in for live and operator-triggered rotations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any

from autonomous_trading_platform.observability.logging import get_logger

logger = get_logger(__name__)

BarRow = tuple[datetime, Any, Any, Any]


class AlpacaDailyBarSource:
    """Satisfies ``CandidateBarSource`` with Alpaca daily bars.

    One batch that fails is retried once, then the error propagates: a silently
    missing batch would reject up to ``batch_size`` names as ``no_market_data`` and
    propose a universe built on partial data.
    """

    source_name = "alpaca_daily"
    expected_bars_per_day = 1

    def __init__(
        self,
        *,
        data_client: Any | None = None,
        feed: Any | None = None,
        batch_size: int = 200,
    ) -> None:
        self._data_client = data_client
        self._feed = feed
        self._batch_size = max(int(batch_size), 1)

    def fetch_bars(
        self, symbols: list[str], *, start: datetime, end: datetime
    ) -> dict[str, list[BarRow]]:
        if not symbols:
            return {}
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = self._data_client or self._default_client()
        feed = self._feed if self._feed is not None else self._default_feed()
        start_naive = _as_utc(start).replace(tzinfo=None)
        end_naive = _as_utc(end).replace(tzinfo=None)

        rows: dict[str, list[BarRow]] = {}
        ordered = sorted(set(symbols))
        n_batches = ceil(len(ordered) / self._batch_size)
        for i in range(n_batches):
            batch = ordered[i * self._batch_size : (i + 1) * self._batch_size]
            request = StockBarsRequest(
                symbol_or_symbols=batch,
                start=start_naive,
                end=end_naive,
                timeframe=TimeFrame.Day,
                feed=feed,
            )
            bar_set = _with_one_retry(client.get_stock_bars, request, batch_index=i)
            data = getattr(bar_set, "data", None) or {}
            for symbol, bars in data.items():
                converted = [
                    (
                        _as_utc(bar.timestamp),
                        bar.close,
                        bar.volume,
                        getattr(bar, "trade_count", None),
                    )
                    for bar in bars
                    if getattr(bar, "timestamp", None) is not None
                ]
                if converted:
                    rows[symbol] = sorted(converted, key=lambda r: r[0])
        logger.info(
            "alpaca_daily_bar_source.fetched",
            extra={
                "symbols_requested": len(ordered),
                "symbols_with_bars": len(rows),
                "batches": n_batches,
                "start": start_naive.isoformat(),
                "end": end_naive.isoformat(),
            },
        )
        return rows

    @staticmethod
    def _default_client() -> Any:
        from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
            get_stock_historical_client,
        )

        return get_stock_historical_client()

    @staticmethod
    def _default_feed() -> Any:
        from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
            get_data_feed,
        )

        return get_data_feed()


def _with_one_retry(call: Any, *args: Any, batch_index: int) -> Any:
    try:
        return call(*args)
    except Exception as exc:
        logger.warning(
            "alpaca_daily_bar_source.batch_retry",
            extra={"batch_index": batch_index, "error": str(exc)},
        )
        return call(*args)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
