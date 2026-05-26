# Domain: Scheduler

## Overview

The scheduler domain is responsible for orchestrating the runtime execution of the trading system on a fixed cadence.

It coordinates ingestion readiness, strategy evaluation, order execution, reconciliation, and risk snapshot generation.

The system operates on a 5-minute bar-based cycle.

---

## Runtime Cadence

The intended runtime model is:

- Evaluation triggered at bar close
- 5-minute intervals
- Strict ordering of steps per cycle

Each cycle represents a single evaluation window.

---

## Trading Cycle Orchestration

The intended runtime sequence is:

1. Bar close detection
2. Data ingestion verification
3. Pre-evaluation reconciliation
4. Strategy evaluation
5. Order creation
6. Order submission
7. Fill monitoring
8. Post-cycle reconciliation
9. Event persistence

---

## Current Behavior

The current implementation uses `run_trading_cycle` as the main orchestration entry point.

Actual steps executed:

1. Ingestion readiness check
2. Strategy evaluation
3. Order submission
4. Order reconciliation
5. Risk snapshot generation

Additional details:

- Cycle timing is based on rounding to 5-minute boundaries
- Ingestion readiness is time-based (deadline check only)
- Airflow DAGs trigger execution every 5 minutes
- RunManifest is updated during execution

---

## Airflow / Scheduled Entry Points

Airflow DAGs are used to schedule:

- Trading cycle (every 5 minutes)
- Ingestion cycle (every 5 minutes)
- Corporate actions (daily)
- Backfill (daily)

DAGs define timing and retries but do not enforce full runtime semantics.

---

## Limitations

The current scheduler implementation is a simplified orchestration layer.

Key limitations:

- No pre-evaluation reconciliation
- Ingestion readiness is time-based, not data-validated
- No enforcement of:
  - corporate action readiness
  - universe snapshot loading
- No SLA decision tree (skip / degrade / halt per symbol)
- Scheduler events (BAR_CLOSED, EVALUATION_STARTED, etc.) not emitted
- Freeze logic is not enforced (stubbed service)
- No human acknowledgment workflow
- No enforcement of runtime invariants before execution

Additionally:

- Expected symbols are hardcoded (no universe integration)
- No separation between fill monitoring and reconciliation
- No end-of-day handling logic

---

## Summary

The scheduler provides a working orchestration loop for the system but does not yet enforce the full runtime guarantees defined in the original design.

It currently acts as a minimal execution coordinator rather than a fully deterministic runtime engine.
