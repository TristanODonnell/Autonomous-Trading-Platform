# Domain: Execution

## Overview

The execution domain is responsible for converting OrderIntents into broker orders, tracking order lifecycle state, processing fills, and updating portfolio state.

It ensures that all execution activity is deterministic, auditable, and aligned with the system’s safety and risk constraints.

---

## Order Flow

The execution pipeline follows:

Signal → OrderIntent → BrokerOrder → Fill

- **OrderIntent**: internal execution instruction
- **BrokerOrder**: broker-facing order representation
- **Fill**: execution event that updates positions and cash

---

## Order State Machine

Orders follow a deterministic lifecycle:

NEW → SUBMITTED → PARTIALLY_FILLED → FILLED
SUBMITTED → CANCELED / REJECTED
PARTIALLY_FILLED → CANCELED

### Properties

- Terminal states are immutable (FILLED, CANCELED, REJECTED)
- Invalid transitions raise errors
- Transitions emit audit log events

---

## Fill Processing & Ledger Updates

Fill events drive portfolio updates:

- PositionLedgerService updates holdings
- CashLedgerService updates balances
- New PositionSnapshot and CashSnapshot records are written

Current behavior:

- Partial and full fills are processed
- Positions and cash are updated incrementally
- No negative quantity allowed in long-only mode

---

## Reconciliation

Reconciliation is intended to verify alignment between internal state and broker state.

### Intended Behavior

- Compare positions, orders, and cash
- Freeze trading on mismatch
- Require human acknowledgment before resuming

### Current Behavior

- Reconciliation is limited to individual order tracking
- Only open orders are reconciled
- Fill updates are applied to internal state
- No full portfolio reconciliation is performed
- No freeze or human acknowledgment workflow exists

---

## Retry Idempotency

`OrderExecutionService.submit()` uses `client_order_id` as an application-level idempotency
key to prevent duplicate broker orders during transient network failures.

### client_order_id

Generated deterministically from `(run_id, strategy_id, bar_timestamp, symbol, side, qty)`
using UUID5. The same intent always produces the same `client_order_id`, and it is never
regenerated between retry attempts.

### Ambiguous failure handling

When submission raises a transport error (timeout, connection reset — any
`httpx.TransportError`), the outcome is ambiguous: the broker may or may not have accepted
the order before the connection failed.

Before retrying, the service performs an idempotency lookup:

```
GET /v2/orders/{client_order_id}?by=client_order_id
```

- **Order found**: treat original submission as successful. Return the existing broker order
  without calling `submit_order` again. Logs `retry_skipped_existing_broker_order`.
- **Order not found**: proceed with retry according to the existing backoff policy.
  Logs `broker_order_not_found_retrying`.
- **Lookup itself fails**: raise the lookup exception immediately. Logs
  `idempotency_lookup_failed`. This prevents any blind retry when broker state is unknown.

### Safe-to-retry failures

HTTP errors (`httpx.HTTPStatusError` — 4xx/5xx) indicate the broker responded definitively.
These are retried according to the existing policy without performing an idempotency lookup,
since a broker response means the submission was not ambiguous.

### Structured log events

| Event | When emitted |
|-------|-------------|
| `ambiguous_submit_failure` | Transport error after `submit_order` |
| `idempotency_lookup_started` | Before calling `get_order_by_client_order_id` |
| `retry_skipped_existing_broker_order` | Lookup found existing order |
| `broker_order_not_found_retrying` | Lookup confirmed order absent |
| `idempotency_lookup_failed` | Lookup raised an exception |
| `order_submission_retry_exhausted` | Max attempts reached |

Fields on each event: `client_order_id`, `symbol`, `side`, `attempt`, `broker`,
`exception_type`, `broker_order_id` (where available).

---

## Fill-Quality Analytics — Two-Phase Persistence (F-04)

`RealisedSlippageService` records fill analytics in two phases that may arrive in any order:

### Phase 1 — `record_submission_context()`

Called immediately after broker order submission. Writes a `fill_quality_metrics` row with:
- reference price and expected slippage from the execution policy
- submission latency and policy context
- all fill actuals (`fill_price`, `fill_timestamp`, slippage metrics) left NULL

### Phase 2 — `record_fill_actuals()`

Called during order reconciliation when a confirmed Fill arrives. Updates the row with:
- actual fill price, fill timestamp, fill latency
- realised slippage (per-share, notional, bps)
- `fill_vs_expected_bps` and `is_adverse_fill` flag

### Ordering Safety

Both phases are idempotent and ordering-safe:

- **Phase 2 before Phase 1**: Phase 2 inserts a minimal row. When Phase 1 subsequently runs, it detects the existing row and merges submission context fields without overwriting fill actuals.
- **Phase 1 before Phase 2**: Normal path. Phase 2 updates the Phase 1 row in place.
- **Repeated Phase 1 calls**: detected; subsequent calls update submission context fields on the existing row.
- **Repeated Phase 2 calls**: the latest fill data wins; a warning is emitted when `fill_id` changes.

Anomalous orderings emit `fill_quality.phase_ordering_anomaly` WARNING log events with `intent_id`, `fill_id`, and `symbol` fields.

---

## Reconciliation — Out-of-Order Broker Update Protection (F-05)

Broker updates are treated as eventually consistent, not perfectly ordered.

### Monotonic Fill Quantity

`extract_incremental_fill()` enforces a monotonic fill quantity invariant:

| Scenario | Behaviour |
|----------|-----------|
| `current_filled_qty > previous` | Normal: extract delta fill, return `new_filled_qty = current` |
| `current_filled_qty == previous` | Duplicate update: no fill extracted, DEBUG log emitted |
| `current_filled_qty < previous` | **Regression detected**: CRITICAL log emitted, `new_filled_qty` preserved at `previous` (no rewind) |

`OrderRuntimeStateService.apply_reconciliation_result()` additionally enforces monotonicity at the persistence layer — it will never write a `previous_filled_qty` lower than the existing tracked value, regardless of what the broker snapshot reports.

### Status Regression Detection

`OrderReconciliationService.reconcile_order()` detects and logs backward status transitions from terminal states (FILLED, CANCELED, REJECTED) to non-terminal states, emitting a `reconciliation.status_regression_detected` WARNING.

### Snapshot Traceability

Fill metadata now includes:

| Field | Content |
|-------|---------|
| `broker_update_timestamp` | `broker_order.updated_at` from the broker snapshot |
| `received_at` | reconciliation cycle wall-clock time |
| `previous_filled_qty` | platform state before this cycle |
| `previous_avg_fill_price` | platform avg price before this cycle |

This enables reconstruction of ordering anomalies from persisted snapshot data.

### Structured Log Events

| Event | Level | When emitted |
|-------|-------|-------------|
| `fill.quantity_regression_detected` | CRITICAL | `current_filled_qty < previous` |
| `fill.duplicate_broker_update` | DEBUG | `current_filled_qty == previous` |
| `fill_quality.phase_ordering_anomaly` | WARNING | Phase ordering inversion or Phase 2 before Phase 1 |
| `fill_quality.repeated_fill_update` | WARNING | `fill_id` changes on a second Phase 2 call |
| `reconciliation.status_regression_detected` | WARNING | Terminal → non-terminal status transition |
| `runtime_state.monotonic_qty_guard_triggered` | WARNING | Persistence-layer regression blocked |

---

## Current Behavior

The execution system is partially implemented and operational:

- Order submission to broker adapter works
- Order state machine enforces valid transitions
- Fill events update portfolio state
- Retry logic is transport-error-aware with idempotency lookup before retry
- Basic audit logging is present

However:

- Strategy lifecycle is not fully driven by execution outcomes
- Reconciliation is incomplete
- Event schema is simplified compared to specification

---

## Limitations

Key limitations in the execution system:

- No full reconciliation across positions, orders, and cash
- No freeze logic on reconciliation mismatch
- No human acknowledgment workflow
- No cancellation of remaining quantity after partial fill
- Strategy state not consistently updated based on fills/rejections
- Event logging does not match full contract (missing fields and structure)
- Idempotency enforcement depends on stubbed components

As a result, execution is functional but lacks full safety and audit guarantees defined in the original design.
