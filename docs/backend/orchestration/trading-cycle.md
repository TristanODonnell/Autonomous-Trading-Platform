# Trading Cycle

## Overview

The trading cycle is the primary runtime loop for the platform. It runs on a 5-minute cadence and coordinates readiness checks, strategy evaluation, order submission, reconciliation, and risk snapshot generation.

The intended runtime design includes bar close detection, ingestion verification, pre-evaluation reconciliation, strategy evaluation, order creation, order submission, fill monitoring, post-cycle reconciliation, and event persistence. In the current implementation, the cycle is a smaller skeleton focused on the core execution path.

---

## Trigger and Cadence

The cycle window is aligned to 5-minute bar boundaries.

Current behavior:
- The cycle timestamp is rounded down to the nearest 5-minute boundary
- A trading cycle window is built with `cycle_start`, `cycle_end`, and `ingestion_deadline`
- The default ingestion deadline is `cycle_end + 60s`
- Airflow schedules the trading cycle every 5 minutes on weekdays

This matches the current scheduler implementation, though it differs from the earlier spec that targeted ingestion by `bar_close + 30s` and evaluation by `bar_close + 35s`.

---

## Current Step Order

The current `run_trading_cycle` implementation executes the following sequence:

1. Ingestion readiness check
2. Trading evaluation
3. Order submission
4. Order reconciliation
5. Risk snapshot

This is the actual implemented order today. Pre-evaluation reconciliation is not part of the current cycle.

---

## Step Details

### 1. Ingestion Readiness

The cycle begins with a readiness check against the ingestion deadline.

Current behavior:
- If current time is before `ingestion_deadline`, readiness returns `ready=True`
- If current time is after the deadline, readiness returns `ready=False`, `safe_mode=True`, with reason `ingestion_deadline_missed`
- The readiness job does not verify that market bars were actually ingested
- It does not verify corporate actions or universe snapshot loading

This means readiness is currently time-based, not data-based.

### 2. Trading Evaluation

If readiness passes, the cycle evaluates the strategy.

Current behavior:
- The trading evaluation job calls `EvaluateStrategyJob`
- Signals are generated
- Portfolio construction generates OrderIntents
- The strategy runtime state is updated on `SIGNAL_GENERATED`

The evaluation step exists, but actual universe membership enforcement and full runtime preconditions are not yet integrated into this path.

### 3. Order Submission

Generated OrderIntents are then submitted.

Current behavior:
- The first intent transitions the strategy state using `ORDER_INTENTS_CREATED`
- Idempotency and throttle checks are invoked
- In non-shadow mode, `order_execution_service.submit()` is called
- The order state machine transitions successful orders from `NEW` to `SUBMITTED`
- Exceptions transition the order to `REJECTED`

Submission is functionally wired, but live gating is not fully enforced here and safety checks depend partly on stubbed readers.

### 4. Order Reconciliation

After submission, the cycle performs order reconciliation.

Current behavior:
- Open tracked orders are listed
- Each tracked order is reconciled against broker order state
- New fills, if any, are persisted
- Post-fill accounting updates positions and cash snapshots
- Tracked order status is updated

This reconciliation is limited to tracked orders and does not perform full portfolio, cash, or buying power reconciliation.

### 5. Risk Snapshot

The cycle ends by computing a risk snapshot.

Current behavior:
- Gross exposure, net exposure, and leverage are computed
- A RiskSnapshot is stored
- This step does not currently block new orders within the same cycle

So risk metrics are captured, but the step is not being used as a final gate inside the cycle itself.

---

## Degraded and Failure Behavior

Current degraded behavior is limited.

If ingestion readiness fails:
- The cycle may skip evaluation and complete in degraded mode if `skip_evaluation_on_ingestion_failure` is enabled
- Otherwise it raises an error

Current limitations:
- No symbol-level skip behavior
- No carry-forward / degrade-safe evaluation path
- No required scheduler event sequence is emitted
- Freeze behavior is stubbed and does not persist or block future actions
- Human acknowledgment flow is not implemented

---

## Current Limitations

The current trading cycle is a minimal runtime skeleton, not the full runtime engine described in earlier planning docs.

Key limitations:
- No pre-evaluation reconciliation
- Readiness check is time-based only
- No corporate action readiness check
- No universe snapshot enforcement in-cycle
- No explicit fill-monitoring phase
- No required scheduler event sequence (`BAR_CLOSED`, `EVALUATION_STARTED`, etc.)
- Freeze handling is effectively a no-op
- No human acknowledgment workflow
- End-of-day cycle semantics are not implemented

The result is a working orchestration path, but not yet a fully deterministic runtime engine.
