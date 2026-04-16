from __future__ import annotations

import pyarrow as pa

UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

BAR_SCHEMA = pa.schema(
    [
        pa.field("bar_id", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("end_timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("interval", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("adjustment_factor", pa.float64(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", UTC_TIMESTAMP, nullable=False),
        pa.field("quality_flags", pa.list_(pa.string()), nullable=True),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

CORPORATE_ACTION_SCHEMA = pa.schema(
    [
        pa.field("action_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("action_type", pa.string(), nullable=False),
        pa.field("effective_date", pa.date32(), nullable=False),
        pa.field("announced_date", pa.date32(), nullable=True),
        pa.field("record_date", pa.date32(), nullable=True),
        pa.field("payable_date", pa.date32(), nullable=True),
        pa.field("split_ratio", pa.float64(), nullable=True),
        pa.field("cash_amount", pa.float64(), nullable=True),
        pa.field("currency", pa.string(), nullable=False),
        pa.field("new_symbol", pa.string(), nullable=True),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", UTC_TIMESTAMP, nullable=False),
        pa.field("metadata", pa.string(), nullable=True),
        pa.field(
            "date", pa.date32(), nullable=False
        ),  # normalized partition/filter date; equals effective_date
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)
