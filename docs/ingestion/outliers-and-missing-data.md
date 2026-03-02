# Outlier Detection + Missing Data Policy (v1)

## Outlier Detection

Outlier checks apply per symbol per bar.

Outlier thresholds (v1):

1) Price spike check
- If close price deviates by > 10x from median close of last 20 bars:
  → mark bar as OUTLIER

2) Range sanity check
- If (high < low) OR any price ≤ 0:
  → reject bar (INVALID)

3) Zero volume check (optional for illiquid names)
- If volume == 0 for 3 consecutive bars:
  → mark symbol as STALE

Outlier handling:
- Outlier bars are excluded from evaluation inputs
- Outlier incident is recorded
- Symbol may be skipped for the cycle

## Missing Bar Semantics (Locked)

Missing MarketBar(T) for a symbol must be resolved deterministically:

Policy (v1):
- If missing for <= 1 bar and last bar exists:
  → FORWARD-FILL close only for indicator continuity
  → BUT: do NOT allow new entries based on forward-filled data
  → exits allowed only if already in position AND risk gates permit

- If missing for 2 consecutive bars:
  → SKIP symbol for evaluation (no signals, no orders)

- If missing for >= 3 consecutive bars OR missing coverage > 5% universe:
  → DEGRADE to safe mode OR HALT per SLA decision tree

## Skip vs Skip Run

v1 locked choice:
- Skip symbol (not skip entire run) unless:
  - missing coverage exceeds threshold OR
  - ingestion SLA hard deadline breached

## Duplicate / Timestamp Integrity

Reject if any of:
- duplicate (symbol, timestamp)
- non-monotonic timestamps for symbol
- bar timestamps not aligned to 5-minute boundaries