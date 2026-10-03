"""AlpacaScreenerProvider — ranks all active US equities by trailing dollar volume.

Satisfies RawSymbolProvider. Uses Alpaca's assets API + historical daily bars
without requiring pre-ingested internal data.

Survivorship caveat: the ranking is point-in-time (bars up to as_of), but the
*membership* step asks Alpaca for assets active **today**. Tickers delisted
since as_of are absent, so for historical replays prefer
PointInTimeIndexProvider (universe/providers/point_in_time_index_provider.py).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any

from autonomous_trading_platform.universe.types import RawSymbolRecord

_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}$")
# Match against both raw string and AssetExchange enum string representations
_TARGET_EXCHANGES = frozenset(
    {
        "NYSE",
        "NASDAQ",
        "ARCA",
        "BATS",
        "AMEX",
        "AssetExchange.NYSE",
        "AssetExchange.NASDAQ",
        "AssetExchange.ARCA",
        "AssetExchange.BATS",
        "AssetExchange.AMEX",
    }
)


class AlpacaScreenerProvider:
    """Ranks active, tradable US equities on Alpaca by trailing dollar volume.

    Calls get_all_assets() for membership, then fetches lookback_days of daily
    bars in batches to score each symbol. Returns the top_n by average daily
    dollar volume, filtered by min_price and min_dollar_volume.

    Ranking uses bars up to as_of, but membership is today's active assets —
    see the module docstring for the survivorship caveat.
    """

    source_name = "alpaca_screener"

    def __init__(
        self,
        *,
        as_of: date,
        top_n: int = 500,
        lookback_days: int = 20,
        min_price: float = 5.0,
        min_dollar_volume: float = 5_000_000.0,
        batch_size: int = 200,
    ) -> None:
        self.as_of = as_of
        self.top_n = top_n
        self.lookback_days = lookback_days
        self.min_price = min_price
        self.min_dollar_volume = min_dollar_volume
        self.batch_size = batch_size

    def fetch_symbols(self) -> list[RawSymbolRecord]:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        from autonomous_trading_platform.config.settings import Settings
        from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
            get_stock_historical_client,
        )

        settings = Settings()
        trading_client = TradingClient(
            settings.broker_api_key,
            settings.broker_api_secret,
            paper=True,
        )
        data_client = get_stock_historical_client()

        # ── Step 1: get all active US equity assets ──────────────────────────
        request = GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
        )
        all_assets = trading_client.get_all_assets(filter=request)

        asset_map: dict[str, object] = {}
        for asset in all_assets:
            symbol = str(getattr(asset, "symbol", "") or "")
            if not symbol or not _SYMBOL_RE.match(symbol):
                continue
            if not getattr(asset, "tradable", False):
                continue
            exchange = str(getattr(asset, "exchange", "") or "")
            if exchange not in _TARGET_EXCHANGES:
                continue
            asset_map[symbol] = asset

        eligible_symbols = sorted(asset_map.keys())

        # ── Steps 2-4: score by trailing dollar volume, filter, rank ─────────
        ranked = rank_symbols_by_dollar_volume(
            data_client=data_client,
            symbols=eligible_symbols,
            as_of=self.as_of,
            lookback_days=self.lookback_days,
            min_price=self.min_price,
            min_dollar_volume=self.min_dollar_volume,
            top_n=self.top_n,
            batch_size=self.batch_size,
        )

        # ── Step 5: build RawSymbolRecord list ───────────────────────────────
        records: list[RawSymbolRecord] = []
        for symbol in ranked:
            asset = asset_map.get(symbol)
            if asset is None:
                continue
            records.append(
                RawSymbolRecord(
                    symbol=symbol,
                    asset_type="us_equity",
                    status="active",
                    is_tradable=True,
                    exchange=str(getattr(asset, "exchange", None) or "") or None,
                    name=str(getattr(asset, "name", None) or "") or None,
                    marginable=getattr(asset, "marginable", None),
                    shortable=getattr(asset, "shortable", None),
                    fractionable=getattr(asset, "fractionable", None),
                    easy_to_borrow=getattr(asset, "easy_to_borrow", None),
                    provider_symbol=symbol,
                    provider_asset_id=str(getattr(asset, "id", "") or "") or None,
                )
            )
        return records


def rank_symbols_by_dollar_volume(
    *,
    data_client: Any,
    symbols: list[str],
    as_of: date,
    lookback_days: int,
    min_price: float,
    min_dollar_volume: float,
    top_n: int,
    batch_size: int = 200,
) -> list[str]:
    """Rank symbols by average daily dollar volume over bars ending at as_of.

    Only bars before as_of are used, so the ranking is point-in-time. Symbols
    with no bars in the window (not yet listed, or already gone) drop out.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    end_dt = datetime.combine(as_of, datetime.min.time())
    # Over-fetch by 7 days to cover weekends/holidays, then score on actual bars
    start_dt = datetime.combine(as_of - timedelta(days=lookback_days + 7), datetime.min.time())

    dollar_volumes: dict[str, float] = {}
    last_prices: dict[str, float] = {}

    n_batches = ceil(len(symbols) / batch_size)
    for i in range(n_batches):
        batch = symbols[i * batch_size : (i + 1) * batch_size]
        try:
            bar_request = StockBarsRequest(
                symbol_or_symbols=batch,
                start=start_dt,
                end=end_dt,
                timeframe=TimeFrame.Day,
                feed="iex",
            )
            bar_set = data_client.get_stock_bars(bar_request)
            for symbol, bars in bar_set.data.items():
                if not bars:
                    continue
                dv_values = [float(b.close) * float(b.volume) for b in bars if float(b.volume) > 0]
                if not dv_values:
                    continue
                dollar_volumes[symbol] = sum(dv_values) / len(dv_values)
                last_prices[symbol] = float(bars[-1].close)
        except Exception:
            # Skip failed batches — partial coverage is acceptable
            continue

    qualified = {
        s: dv
        for s, dv in dollar_volumes.items()
        if dv >= min_dollar_volume and last_prices.get(s, 0.0) >= min_price
    }
    return sorted(qualified, key=lambda s: -qualified[s])[:top_n]
