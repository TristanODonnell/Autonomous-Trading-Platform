# Runtime Engine (v1) — Index

## Purpose

This folder locks deterministic runtime behavior for live and paper execution environments.

The runtime layer governs:

- 5-minute evaluation cadence
- Scheduler enforcement
- Order state transitions
- Strategy lifecycle transitions
- Reconciliation enforcement
- Freeze and human-acknowledgment semantics
- Global event recording contract

No runtime behavior may violate these specifications.

These rules are binding for v1 implementation.

---

## Canonical References

- [Runtime Lifecycle Semantics](lifecycle-semantics.md)
  Global runtime invariants, freeze conditions, and event recording contract.

- [Scheduler Model (5-Minute Cadence)](scheduler-model.md)
  Canonical time model, ingestion SLA, evaluation preconditions, and cycle event ordering.

- [Order State Machine](order-state-machine.md)
  Deterministic order transitions, terminal states, retry policy, and forbidden transitions.

- [Strategy Lifecycle State Machine](strategy-state-machine.md)
  Strategy position ownership rules, state transitions, and invalid transition handling.

- [Reconciliation Model](reconciliation.md)
  Position alignment rules, mismatch handling, human acknowledgment requirements, and freeze triggers.

---

## Runtime Guarantees (v1)

The runtime engine must guarantee:

- No evaluation on partially formed bars
- No order exists without originating OrderIntent
- No position exists without broker-confirmed fill
- No ambiguous state transitions
- Reconciliation before capital deployment
- Terminal states are immutable
- All transitions emit exactly one immutable event
- All critical failures freeze trading

There are no implicit transitions.

Every state change must have:

- A single explicit trigger
- A single recorded event
- Persistence before subsequent transition

---

## Pages

- [Runtime Lifecycle Semantics](lifecycle-semantics.md)
- [Scheduler Model](scheduler-model.md)
- [Order State Machine](order-state-machine.md)
- [Strategy State Machine](strategy-state-machine.md)
- [Reconciliation](reconciliation.md)


## Freeze Conditions (v1)

Trading must freeze when:

- Reconciliation mismatch detected
- Broker connectivity unstable
- Ingestion SLA breached beyond tolerance
- Kill switch engaged
- Invalid state transition detected

Freeze behavior:

- Cancel open orders
- Block new OrderIntent creation
- Emit CRITICAL alert
- Require HUMAN_ACK_GRANTED event before resume

---

## Non-Goals (v1)

- No sub-bar execution
- No async multi-strategy coordination
- No cross-asset concurrency
- No adaptive scheduling
- No speculative execution

Runtime focuses strictly on deterministic correctness, auditability, and safety enforcement.
