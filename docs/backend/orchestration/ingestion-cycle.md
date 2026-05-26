# Ingestion Cycle

## Overview

The ingestion cycle is responsible for producing the market data used by downstream strategy and execution steps. It fetches source bars, aggregates them into 5-minute windows, validates them, records ingestion anomalies, and persists the resulting data.

The current implementation provides a simplified ingestion flow with basic missing-bar detection, late-bar detection, and outlier flagging.

---

## Current Flow

The current ingestion cycle operates on a single 5-minute window.

At a high level it performs:

1. Fetch minute bars from provider
2. Accumulate bars for the cycle window
3. Aggregate minute bars into 5-minute bars
4. Validate aggregated bars
5. Record ingestion anomalies
6. Persist bars
7. Finalize cycle and compute missing coverage

This flow exists today, but it does not implement the full staged SLA model originally described in the older ingestion planning docs.

---

## Market Data Fetch and Aggregation

Current behavior:
- Minute bars are fetched from Alpaca market data
- The ingestion job accumulates bars until the cycle timestamp advances
- Aggregation produces 5-minute bars from minute-bar buckets
- Aggregation enforces exactly five consecutive one-minute bars
- Duplicate or misaligned timestamps cause errors

This means timestamp integrity and 5-minute window formation are among the better-enforced parts of the ingestion pipeline.

---

## Validation and Quality Checks

### Late Bars

Current behavior:
- Late-bar checks use a 30-second allowed delay
- Bars arriving after that threshold are flagged with `BAR_LATE`
- Late bars are still persisted

### Outliers

Current behavior:
- Outliers are flagged when close price deviates by more than about 20% from the previous close
- Outlier bars are recorded with `BAR_OUTLIER`
- Outlier bars are still persisted

### Missing Bars

Current behavior:
- Missing symbols are logged individually
- The cycle computes a single `missing_ratio`
- If the missing ratio exceeds 20%, the cycle records `SLA_BREACH` and raises an error

There is no consecutive-missing-bar tracking, no forward fill, and no per-symbol skip logic.

---

## Corporate Actions Within the Ingestion Flow

Corporate action ingestion exists as a separate daily flow.

Current behavior:
- Corporate actions are fetched from Alpaca
- Only a subset of action types are handled in practice
- Split adjustments are applied to historical data
- No continuity validation is performed across split boundaries
- Corporate actions are not tightly integrated into the per-bar ingestion cycle

So while corporate action handling exists, it is not part of a unified “ingestion cycle readiness” gate for the trading loop.

---

## Event Logging

The ingestion flow records events through the audit logging service.

Current event types include:
- `BAR_MISSING`
- `BAR_LATE`
- `BAR_OUTLIER`
- `SLA_BREACH`
- `PARSE_FAILED`
- `CORPORATE_ACTION_VALIDATION_FAILED`

The event model is simplified relative to the original planning docs. It does not include the richer schema proposed for ingestion incidents, such as severity, action taken, dataset version, or universe version fields.

---

## SLA Behavior

The current ingestion cycle does not implement the original 30s/90s staged decision tree.

Current behavior:
- There is no waiting for a freshness window before evaluation
- There is no hard 90-second halt window
- There is no skip/degrade/halt branching based on 1% / 5% missing coverage
- Evaluation proceeds immediately after ingestion unless a missing-ratio threshold error is raised

In practice, the SLA behavior today is much simpler:
- log missing bars
- compute missing ratio
- fail if missing ratio exceeds a hard-coded threshold

That is the current real behavior.

---

## Current Limitations

Key limitations in the ingestion cycle:

- No staged SLA enforcement windows
- No symbol-level skip behavior
- No forward-fill handling
- No safe-mode / degrade evaluation path
- Outlier detection differs from the original statistical design
- Corporate action continuity checks are not implemented
- Event schema is simplified
- Provider rate-limit/backoff behavior is not fully implemented
- Produced artifacts are not fully version-pinned to universe and dataset metadata

The ingestion cycle is therefore functional, but still much simpler than the original v1 planning design.
