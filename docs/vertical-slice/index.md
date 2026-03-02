# Vertical Slice (v1) — Index

## Pages

- [Strategy Spec](strategy-spec.md)
- [Execution Constraints](execution-constraints.md)
- [Failure Semantics](failure-semantics.md)

## Purpose

This folder locks the exact functional scope of the v1 vertical slice.

The vertical slice defines:

- The single strategy implemented
- Supported order types
- Execution constraints
- Failure semantics
- Capital deployment rules

This is the minimum end-to-end system that must run deterministically in research, shadow, and paper modes before expansion.

These rules are binding for v1.

---

## Canonical References

- [v1 Strategy Specification](strategy-spec.md)
  Strategy logic, entry/exit rules, position sizing, risk constraints, and cooldown behavior.

- [Execution Constraints](execution-constraints.md)
  Supported order types, time-in-force rules, extended-hours behavior, and idempotency requirements.

- [Failure Semantics](failure-semantics.md)
  Missing data handling, broker rejection behavior, partial fill logic, capital breach response, and kill switch liquidation rules.

---

## Vertical Slice Guarantees (v1)

The vertical slice must guarantee:

- Single strategy only
- Single capital bucket
- Single universe
- 5-minute bar cadence
- Market and Limit orders only
- No margin
- No leverage
- No short selling
- Deterministic signal → intent → execution flow

This slice must run identically across:

- Research mode
- Shadow mode
- Paper mode

Live mode is out of scope for v1.

---

## Execution Scope (Locked)

Supported:

- Market orders (default)
- Limit orders (optional)
- DAY time-in-force
- Fractional shares

Explicitly not supported:

- Stop orders
- GTC
- IOC / FOK
- Extended-hours trading (default off)
- Multi-strategy orchestration
- Short selling
- Margin



---

## Failure Philosophy (v1)

When errors occur:

- No silent retries
- No ambiguous state
- No partial undefined behavior
- No hidden capital deployment

All failures must:

- Emit event
- Be recorded
- Follow deterministic state transition rules

---

## Non-Goals (v1)

- No multi-strategy system
- No cross-asset trading
- No portfolio optimization
- No dynamic parameter tuning
- No live deployment enablement

Vertical slice focuses strictly on proving that one deterministic strategy can flow through the full system safely and reproducibly.
