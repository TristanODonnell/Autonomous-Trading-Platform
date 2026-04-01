# Runtime Lifecycle Semantics (v1)

## Purpose

This document defines deterministic runtime behavior for:

- 5-minute evaluation cadence
- Order execution state machine
- Strategy lifecycle state machine
- Reconciliation enforcement
- SLA and failure handling

This document is binding for v1.

No runtime behavior may violate this specification.

---

# Runtime Architecture Overview

The runtime loop executes on a strict 5-minute bar cadence.

Each cycle consists of:

1. Bar close detection
2. Data ingestion verification
3. Reconciliation (pre-evaluation)
4. Strategy evaluation
5. Order creation
6. Order submission
7. Fill monitoring
8. Post-cycle reconciliation
9. Event persistence

All transitions are event-driven and recorded.

There are no implicit transitions.

---

# Global Runtime Invariants

1. No evaluation occurs on partially formed bars.
2. No order exists without an originating OrderIntent.
3. No position exists without confirmed broker fill.
4. No strategy state transition occurs without a triggering event.
5. Reconciliation must pass before new capital deployment.
6. Terminal states are immutable.
7. There is no ambiguous "unknown" state.

---

# Runtime Freeze Conditions

The engine must freeze trading when:

- Reconciliation mismatch detected
- Broker connectivity unstable
- Data ingestion SLA missed beyond tolerance
- Kill switch engaged

Freeze behavior:

- Cancel open orders
- Block new OrderIntents
- Emit CRITICAL alert
- Require human acknowledgment to resume

# Event Recording Contract (Global)

## Rule
Every state transition MUST:
- have a single explicit trigger
- emit a single immutable event
- be persisted before any subsequent transition occurs

## Minimum Event Fields
All runtime events MUST include:

- event_id (uuid)
- event_type (enum)
- event_time_utc
- run_id
- strategy_id (nullable for global events)
- entity_type (order, strategy, reconciliation, scheduler)
- entity_id (nullable for scheduler global events)
- from_state (nullable)
- to_state (nullable)
- trigger (string)
- payload (json)
- hash_prev (for chaining, optional v1+)

No component may mutate or delete events.
