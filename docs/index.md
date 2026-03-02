# Retail Autonomous Trading Platform (v1)

## System Definition

A deterministic, audit-first retail algorithmic trading platform for U.S. equities using 5-minute bars, a single strategy, a single universe, and strict capital protection guarantees.

v1 operates in:

- Research mode
- Shadow mode
- Paper trading mode

Live trading is out of scope for v1.

---

## Architecture Overview

The system is organized into the following subsystems:

### Contracts
Canonical data contracts shared across research and execution environments.

### Ingestion
Market data ingestion, SLAs, corporate actions, and data integrity rules.

### Research
Deterministic backtesting engine with fill model, cost model, and experiment tracking.

### Runtime
5-minute scheduler, order state machine, reconciliation model, and lifecycle semantics.

### Safety
Environment isolation, layered enablement gates, caps & throttles, idempotency, and kill switch.

### Universe
Time-aware universe snapshots and survivorship-bias controls.

### Vertical Slice
Single-strategy baseline (SMA_Cross_v1) with locked execution constraints.

### Paper Validation
Operational proof standards required before promotion.

---

## Core Docs

- [v1 Boundaries](v1-boundaries.md)
- [Safety Doctrine](safety-doctrine.md)
- [Compile vs Runtime](compile-vs-runtime.md)
- [Decisions](decisions.md)
- [Invariants](invariants.md)

---

## Version

v1 — 5-minute bars
Alpaca broker
Single strategy
Single universe
Single capital bucket
Bar-based evaluation only
