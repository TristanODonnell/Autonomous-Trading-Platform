import pyarrow as pa

from autonomous_trading_platform.contracts.market.market_bar import MarketBar


def bars_to_arrow(bars: list[MarketBar]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "bar_id": b.bar_id,
                "timestamp": b.timestamp,
                "end_timestamp": b.end_timestamp,
                "interval": b.interval.value,
                "symbol": b.symbol,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": b.volume,
                "vwap": float(b.vwap) if b.vwap is not None else None,
                "trade_count": b.trade_count,
                "price_basis": b.price_basis.value,
                "adjustment_factor": float(b.adjustment_factor),
                "source": b.source,
                "ingested_at": b.ingested_at,
                "quality_flags": [f.value for f in b.quality_flags],
            }
            for b in bars
        ]
    )
