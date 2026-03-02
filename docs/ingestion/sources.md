# Ingestion Sources (v1)

## Market Data Provider

Primary provider: Alpaca market data (free feeds).

v1 feeds (non-paid):
- IEX (primary, free)
- Delayed SIP (where available)
- Blue Ocean ATS / overnight feed (24/5 coverage where supported)

v1 interval:
- 5-minute bars (derived from minute bars or direct bar endpoint as available)

## Corporate Actions Provider

Primary: Alpaca corporate actions (as available via account/broker data endpoints).

Corporate action types tracked:
- cash_dividend
- stock_dividend
- split_forward
- split_reverse
- merger_cash
- merger_stock
- spinoff
- name_change

## Universe Source

Universe: "Alpaca IEX Top 500" (as defined in Phase 3 universe governance docs).
Universe membership is time-aware and versioned.

## Rate Limits & Request Discipline (Planning-Level)

All ingestion must obey provider limits.

Policy:
- Prefer batch endpoints over per-symbol loops
- Use bounded concurrency (max_workers defined in config)
- Backoff on 429 / 5xx with exponential backoff and jitter
- Hard cap retries per cycle; do not exceed evaluation window

## Ingestion Artifacts Produced (Per Cycle)

For bar timestamp `T`:
- MarketBar dataset for all universe symbols at timestamp `T`
- UniverseSnapshot reference (version pinned)
- CorporateAction events effective at or before `T` (as configured)

All artifacts must be pinned to DatasetVersion / UniverseVersion via RunManifest.