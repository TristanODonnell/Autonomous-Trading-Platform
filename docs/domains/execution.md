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

## Current Behavior

The execution system is partially implemented and operational:

- Order submission to broker adapter works
- Order state machine enforces valid transitions
- Fill events update portfolio state
- Retry logic exists for network errors
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
- Retry logic does not distinguish error types (may retry incorrectly)
- Event logging does not match full contract (missing fields and structure)
- Idempotency enforcement depends on stubbed components

As a result, execution is functional but lacks full safety and audit guarantees defined in the original design.
