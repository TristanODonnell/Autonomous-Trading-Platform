
## Pages

- [Safety Invariants](safety-invariants.md)
- [Environment Model](environment-model.md)
- [Layered Enablement Gates](layered-enablement-gates.md)
- [Broker Account Allowlist](broker-account-allowlist.md)
- [Caps and Throttles](caps-and-throttles.md)
- [Idempotency and Dedupe](idempotency-and-dedupe.md)
- [Shadow Mode](shadow-mode.md)


# Safety & Capital Protection (v1) — Index

## Purpose

This folder locks the non-negotiable safety architecture for the trading platform.

The safety layer guarantees:

- Environment isolation (paper vs live)
- Capital exposure limits
- Duplicate prevention
- Broker routing protection
- Multi-layer live enablement gates
- Shadow mode enforcement
- External kill-switch enforcement

No single bug, misconfiguration, or database mutation may route unintended capital.

These rules are binding for v1 implementation.

---

## Canonical References

- [Safety Invariants](safety-invariants.md)
  Global non-negotiable safety guarantees across environments.

- [Environment Model](environment-model.md)
  Strict paper vs live isolation rules and namespace separation.

- [Layered Enablement Gates](layered-enablement-gates.md)
  Build-time, configuration, runtime, and external kill-switch gating required before live execution.

- [Broker Account Allowlist](broker-account-allowlist.md)
  Environment-scoped broker account validation and routing constraints.

- [Hard Caps & Throttles](caps-and-throttles.md)
  Exposure caps, notional limits, and order-rate constraints enforced pre-execution.

- [Idempotency & Duplicate Prevention](idempotency-and-dedupe.md)
  Deterministic idempotency key generation and duplicate submission blocking.

- [Shadow Mode Specification](shadow-mode.md)
  Non-executing validation mode guaranteeing zero broker interaction.

---

## Safety Guarantees (v1)

The safety system must guarantee:

- Paper and live environments are physically and logically isolated
- Live trading requires all enablement gates to pass
- OrderIntent must pass:
  - Idempotency validation
  - Cap validation
  - Allowlist validation
  - Environment validation
- No broker submission occurs before all safety checks pass
- No duplicate submissions are possible
- Kill switch exists outside primary service and database
- Shadow mode cannot initialize broker adapter

Live routing requires:

Build Gate
AND Configuration Gate
AND Runtime Human Confirmation
AND External Kill Switch Inactive

Failure of any gate blocks execution.

---

## Freeze & Hard-Fail Philosophy

Safety violations must:

- Hard fail before broker interaction
- Emit audit log entry
- Prevent execution for the current cycle
- Trigger freeze when invariant breach is critical

Silent degradation is not permitted.

---

## Non-Goals (v1)

- No automated risk override
- No dynamic exposure tuning
- No probabilistic safety thresholds
- No adaptive gate relaxation
- No implicit live enablement

Safety focuses strictly on deterministic capital protection and auditability.
