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
