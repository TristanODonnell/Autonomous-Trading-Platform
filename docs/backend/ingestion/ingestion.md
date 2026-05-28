# Domain: Ingestion

## Overview

The ingestion system is responsible for retrieving market data, validating and aggregating it into 5-minute bars, applying corporate actions, and producing datasets used by downstream systems (strategy, execution, risk).

The system operates on a per-cycle basis aligned to 5-minute bar intervals.

---

## Data Sources

The system uses Alpaca as the primary data provider:

- Market data: IEX feed (free tier)
- Interval: 5-minute bars (aggregated from minute data)
- Corporate actions: Alpaca corporate actions API

Data is fetched per cycle and processed into canonical MarketBar records.

---

## Core Pipeline Behavior

Each ingestion cycle processes data for a single 5-minute bar window.

Current behavior:

- Minute bars are fetched and aggregated into 5-minute bars
- Aggregation enforces:
  - exactly 5 consecutive minute bars per window
  - strict timestamp alignment
  - no duplicate timestamps
- Missing bars are tracked per symbol
- A missing ratio is computed across the universe
- If missing coverage exceeds a threshold (~20%), the cycle fails

There is no staged SLA enforcement or delayed evaluation window; ingestion and evaluation occur immediately after data retrieval.

---

## Data Validation & Quality

The ingestion system performs basic validation and anomaly detection:

### Validation Rules
- Reject invalid bars (e.g., negative prices, malformed ranges)
- Enforce monotonic timestamps per symbol
- Enforce 5-minute alignment boundaries

### Late Data
- Bars arriving after ~30 seconds past expected time are flagged as late
- Late bars are still persisted and used

### Outlier Detection
- Outliers are detected using a simple deviation rule:
  - ~20% deviation from previous close
- Outliers are flagged but still persisted

### Missing Data Handling
- Missing bars are logged per symbol
- No forward-fill logic is applied
- No multi-bar missing tracking exists
- No per-symbol skip logic during evaluation

---

## Corporate Actions Handling

The system processes corporate actions and applies adjustments to historical data:

- Raw market data is preserved
- Adjusted price series are generated using split adjustment factors

Current behavior:

- Only a subset of corporate action types are processed (e.g., cash dividends, reverse splits)
- Split adjustments are applied to historical data
- Adjusted and raw series are stored separately

Limitations:

- No continuity validation across split boundaries (as originally specified)
- No automatic exclusion of symbols on adjustment inconsistencies
- Limited coverage of corporate action types compared to original design

---

## Observability (Events / Logging)

The ingestion system records events during processing using a centralized audit logging service.

Current event types include:

- BAR_MISSING
- BAR_LATE
- BAR_OUTLIER
- PARSE_FAILED
- CORPORATE_ACTION_VALIDATION_FAILED

Event payloads include:

- run_id
- event type
- timestamp
- metadata

Limitations:

- Event schema is simplified compared to original design
- Missing:
  - severity levels (INFO/WARN/CRITICAL)
  - action classification (skip / degrade / halt)
  - dataset_version / universe_version linkage
- SLA-specific events (INGESTION_SLA_PASSED / MISSED) are not implemented

---

## Limitations

The current ingestion system is a simplified implementation of the original design.

Key limitations:

- SLA decision tree (skip / degrade / halt) is not implemented
- No time-based ingestion windows (30s / 90s deadlines)
- No forward-fill or multi-bar missing data handling
- Outlier detection uses a simplified rule instead of rolling statistical checks
- Corporate action continuity validation is not enforced
- Event logging does not match the full audit/event contract
- No provider rate-limit handling or retry/backoff strategy
