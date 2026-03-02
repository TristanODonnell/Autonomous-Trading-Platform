# Data SLAs + Fallback Rules (v1)

## Definitions

bar_timestamp `T`:
- start time of 5-minute bar window [T, T+5m)
- bar_close_time = T+5m (UTC-aligned)

freshness window:
- time after bar_close_time during which ingestion must complete

allowed lateness tolerance:
- maximum delay permitted before the system must degrade/skip/halt

## SLA Targets (Per 5-Min Cycle)

SLA-1: Freshness Target
- MarketBar(T) must be available by:
  bar_close_time + 30s

SLA-2: Absolute Lateness Tolerance (Hard Deadline)
- If MarketBar(T) not available by:
  bar_close_time + 90s
  → system must HALT evaluation for this cycle

## Breach Actions (Deterministic)

If ingestion is late/incomplete at bar_close_time + 30s:

Decision Tree:

1) If missing symbols ≤ 1% of universe AND missing symbols are non-critical:
   → SKIP evaluation for missing symbols only

2) Else if missing symbols ≤ 5% AND last-known-safe values exist:
   → DEGRADE (safe mode):
      - no new entries
      - exits allowed only if risk/freeze policy permits
      - forward-fill allowed only under missing-data policy

3) Else:
   → HALT cycle:
      - no evaluation
      - no new OrderIntents
      - alert emitted

At bar_close_time + 90s (hard deadline):
- HALT cycle regardless of partial coverage

## Recording Requirements

Each cycle must emit exactly one of:
- INGESTION_SLA_PASSED
- INGESTION_SLA_MISSED

If missed, record:
- number of missing symbols
- list of missing symbols (bounded, or hash + sample)
- cause (timeout, provider error, rate limited, partial response)
- chosen action: SKIP / DEGRADE / HALT
- timestamps: bar_close_time, first_attempt_time, final_attempt_time