import pyarrow as pa

from autonomous_trading_platform.contracts.market.market_bar import MarketBar


def bars_to_arrow(bars: list[MarketBar]) -> pa.Table:
    table = pa.Table.from_pylist(
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
    # PyArrow may infer string columns as dictionary<string> when there are few unique
    # values (e.g. single-symbol per-partition writes). Cast to plain string so all
    # Parquet files in the same partition have a compatible schema.
    for col in ("symbol", "interval", "price_basis", "source"):
        idx = table.schema.get_field_index(col)
        if idx >= 0 and pa.types.is_dictionary(table.schema.field(col).type):
            table = table.set_column(idx, col, table.column(col).cast(pa.string()))
    # PyArrow infers quality_flags as list<null> when all bars have empty flag lists.
    # Force list<string> so every partition file has a consistent schema.
    qf_idx = table.schema.get_field_index("quality_flags")
    if qf_idx >= 0:
        table = table.set_column(
            qf_idx,
            "quality_flags",
            table.column("quality_flags").cast(pa.list_(pa.string())),
        )
    return table
