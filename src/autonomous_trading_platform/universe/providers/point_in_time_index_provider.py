"""PointInTimeIndexProvider — survivorship-safe candidate pool for historical replays.

Satisfies RawSymbolProvider. Membership comes from bundled point-in-time index
constituents (who was in the S&P 500 on as_of), not from Alpaca's list of
assets active today, so companies that were later acquired, went bankrupt or
left the index are still candidates on dates when they existed. Ranking reuses
the AlpacaScreenerProvider dollar-volume rule over bars up to as_of.

Symbols are filtered to plain 1–6 letter tickers, matching AlpacaScreenerProvider
(share-class tickers such as BRK.B are excluded by both).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from autonomous_trading_platform.universe.providers.alpaca_screener_provider import (
    rank_symbols_by_dollar_volume,
)
from autonomous_trading_platform.universe.services.index_constituents_service import (
    IndexConstituents,
    load_sp500_constituents,
)
from autonomous_trading_platform.universe.types import RawSymbolRecord

_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}$")

SP500_POINT_IN_TIME = "sp500_point_in_time"
ALPACA_ACTIVE = "alpaca_active"
SCREENER_SOURCES = frozenset({SP500_POINT_IN_TIME, ALPACA_ACTIVE})


def _default_data_client() -> Any:
    from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
        get_stock_historical_client,
    )

    return get_stock_historical_client()


class PointInTimeIndexProvider:
    source_name = SP500_POINT_IN_TIME

    def __init__(
        self,
        *,
        as_of: date,
        top_n: int = 500,
        lookback_days: int = 20,
        min_price: float = 5.0,
        min_dollar_volume: float = 5_000_000.0,
        batch_size: int = 200,
        constituents: IndexConstituents | None = None,
        data_client_factory: Callable[[], Any] = _default_data_client,
    ) -> None:
        self.as_of = as_of
        self.top_n = top_n
        self.lookback_days = lookback_days
        self.min_price = min_price
        self.min_dollar_volume = min_dollar_volume
        self.batch_size = batch_size
        self._constituents = constituents
        self._data_client_factory = data_client_factory

    def members(self) -> list[str]:
        constituents = self._constituents or load_sp500_constituents()
        return [s for s in constituents.members_as_of(self.as_of) if _SYMBOL_RE.match(s)]

    def fetch_symbols(self) -> list[RawSymbolRecord]:
        ranked = rank_symbols_by_dollar_volume(
            data_client=self._data_client_factory(),
            symbols=self.members(),
            as_of=self.as_of,
            lookback_days=self.lookback_days,
            min_price=self.min_price,
            min_dollar_volume=self.min_dollar_volume,
            top_n=self.top_n,
            batch_size=self.batch_size,
        )
        return [
            RawSymbolRecord(
                symbol=symbol,
                asset_type="us_equity",
                # Status as of as_of: it was an index member trading then.
                status="active",
                is_tradable=True,
                provider_symbol=symbol,
            )
            for symbol in ranked
        ]


def build_universe_screener(source: str, *, as_of: date, top_n: int) -> Any:
    """RawSymbolProvider for a replay's configured screener source."""
    if source == SP500_POINT_IN_TIME:
        return PointInTimeIndexProvider(as_of=as_of, top_n=top_n)
    if source == ALPACA_ACTIVE:
        from autonomous_trading_platform.universe.providers.alpaca_screener_provider import (
            AlpacaScreenerProvider,
        )

        return AlpacaScreenerProvider(as_of=as_of, top_n=top_n)
    raise ValueError(f"Unknown screener source {source!r}; valid: {sorted(SCREENER_SOURCES)}")
