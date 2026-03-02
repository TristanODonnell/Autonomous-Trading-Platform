# MarketBar

## Purpose
- Canonical 5-minute OHLCV bar used for strategy evaluation, backtests, and ledger valuation.

## Producer / Consumer
- Produced by: Market Data Ingestion (Broker/Provider adapter) OR Backtest Data Loader
- Consumed by: Strategy Engine, Backtester, Valuation/Ledger, Risk Engine

## Schema (Canonical Fields)

| Field | Type | Required | Description |
|---|---|---:|---|
| `bar_id` | string | yes | Deterministic ID. Recommend: `"{symbol}:{interval}:{timestamp}:{price_basis}"`. |
| `timestamp` | datetime (UTC) | yes | Bar **start** time (inclusive). |
| `end_timestamp` | datetime (UTC) | yes | Bar end time (exclusive). Must equal `timestamp + interval`. |
| `interval` | enum | yes | For v1: fixed `"5m"`. |
| `symbol` | string | yes | Ticker symbol at that time. |
| `open` | float | yes | First trade price in bar. |
| `high` | float | yes | Highest trade price in bar. |
| `low` | float | yes | Lowest trade price in bar. |
| `close` | float | yes | Last trade price in bar. |
| `volume` | int | yes | Shares traded in bar. |
| `vwap` | float | no | VWAP if provided; else null. |
| `trade_count` | int | no | Trade count if provided; else null. |
| `price_basis` | enum | yes | `"raw"` or `"adjusted"`. |
| `adjustment_factor` | float | yes | 1.0 for raw; else cumulative factor for adjusted bars. |
| `source` | string | yes | Provider/feed identifier. |
| `ingested_at` | datetime (UTC) | yes | When your system stored it (lineage / SLA). |
| `quality_flags` | list[string] | no | E.g. `["late","gap_fill","outlier_suspect"]`. |


## Invariants (Must Always Be True)
- **Alignment:** `timestamp` must be aligned to 5-minute boundaries in UTC:
  - minute ∈ {00,05,10,...,55}, second=0, microsecond=0.
- **Duration:** `end_timestamp = timestamp + 5 minutes`.
- **Key uniqueness:** `(symbol, interval, timestamp, price_basis)` is unique (idempotent upsert).
- **Price sanity:** `high >= max(open, close, low)` and `low <= min(open, close, high)`.
- **Non-negatives:** `volume >= 0`, `trade_count >= 0` when present.
- **Raw vs adjusted:** if `price_basis="raw"` then `adjustment_factor == 1.0`.
- **Monotonic within symbol:** bars for a symbol must not overlap; `timestamp` strictly increases by interval when complete.

## Validation Rules (Planning-Level)
- Check: reject if timestamp misaligned, duration wrong, OHLC violates bounds, negative volume, or symbol missing.
- Check: outlier screen (planning): flag if abs(log-return) > threshold vs trailing window (do not auto-delete).
- Missing bar policy (v1):
  - If a bar is missing at evaluation time: mark `quality_flags += ["missing"]` and **skip evaluation for that symbol** (do not synthesize prices).
  - If many symbols missing: **halt strategy cycle** and alert.
- Late bar policy:
  - If bar arrives after you already evaluated that window: store it, flag `["late"]`, and do **not** retroactively change prior decisions in the same run.

## Versioning
- `schema_version`: integer (start at `1`). Increment only when fields/invariants change.
- `data_version`: string (e.g., `md:alpaca:v1:2026-02-27`). Stored per dataset build/ingest partition and recorded in RunManifest as `dataset_version`.
- Backward compatibility:
  - Minor additive fields: allowed (consumers must ignore unknown fields).
  - Breaking changes (rename/remove/change meaning/invariants): require `schema_version += 1` and dual-read period.
