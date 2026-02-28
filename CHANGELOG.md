# Changelog

## v0.1.0 — Phase 0 Spec Artifacts Locked

Added:
- v1 boundaries and explicit non-goals
- Safety doctrine (capital protection invariants)
- Compile-time vs runtime semantics
- Canonical contracts index (v1)
- Global invariants list
- Decision log scaffold

Notes:
This release locks the non-negotiable architecture and safety constraints for v1 before implementation begins.

## v0.2.0 — Phase 1 Canonical Contracts Locked

Added:
- Full canonical contract definitions:
  - MarketBar
  - CorporateAction
  - UniverseSnapshot
  - Signal
  - OrderIntent (idempotency guarantees)
  - BrokerOrder (lifecycle state machine)
  - Fill (execution truth)
  - PositionSnapshot
  - CashSnapshot
  - RiskSnapshot
  - RunManifest (reproducibility root)
- Formal invariants for:
  - Bar alignment and monotonicity
  - Corporate action continuity
  - Universe time-awareness (no survivorship leakage)
  - Order idempotency
  - Capital safety
  - Risk enforcement
- Reproducibility constraints:
  - Dataset and universe version pinning
  - Strategy config snapshot
  - Git commit pinning
  - Conditional backtest determinism rules

Notes:
This release freezes the canonical contract layer before implementation begins.
All execution and ledger behavior must conform to these contracts.