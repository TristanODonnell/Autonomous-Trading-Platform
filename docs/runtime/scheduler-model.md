# Scheduler Model (5-Minute Cadence)

## Canonical Time Model

- All timestamps stored in UTC
- Market calendar derived from NYSE
- Evaluation strictly triggered by bar close

Bar interval: [T, T+5m)
Bar close timestamp: T+5m

Evaluation time = bar_close_time

---

# Ingestion SLA

Data must be fully ingested by:

bar_close_time + 30 seconds

Evaluation must begin no later than:

bar_close_time + 35 seconds

---

# Evaluation Preconditions

Evaluation may only start if:

- All MarketBars ingested
- Corporate actions applied
- Universe snapshot loaded
- Prior reconciliation completed

---

# SLA Miss Decision Tree

IF ingestion incomplete:

    IF small symbol subset missing:
        → Skip affected symbols
    ELSE IF recoverable via last-known-safe:
        → Degrade (carry forward)
    ELSE:
        → Halt evaluation cycle

Halt behavior:
- No signals generated
- No orders submitted
- Alert emitted

---

# End-of-Day Semantics

At session close:

- Final reconciliation runs
- All DAY orders expected resolved
- Strategy states persisted
- Snapshot saved

## Scheduler Cycle Events (Required)

Each evaluation cycle MUST emit the following events in order:

1. BAR_CLOSED (bar_timestamp)
2. INGESTION_SLA_PASSED or INGESTION_SLA_MISSED
3. RECONCILIATION_STARTED
4. RECONCILIATION_PASSED or RECONCILIATION_FAILED
5. EVALUATION_STARTED
6. EVALUATION_COMPLETED (with counts: signals, intents)
7. EXECUTION_WINDOW_STARTED
8. EXECUTION_WINDOW_COMPLETED
9. CYCLE_COMPLETED

These events are immutable and written to the audit log.
