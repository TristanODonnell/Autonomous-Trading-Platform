# Research Engine Overview

## Purpose

Define the deterministic research engine used for backtesting and scenario analysis.

The research engine MUST share the same data contracts as live and paper trading:

- MarketBar
- Signal
- OrderIntent
- BrokerOrder
- Fill
- PositionSnapshot
- RunManifest

The research engine is not allowed to invent alternate data shapes.

---

## Core Design Principle

Research and live must produce the same output structure.

Shape equivalence requirement:

Signal
→ OrderIntent
→ BrokerOrder (simulated)
→ Fill events (simulated)
→ Position updates
→ Reports

No research-only shortcuts are allowed.

---

## v1 Simulation Model

- Bar-based simulation
- Event-like order state transitions
- Deterministic clock
- No tick-level replay
- No order book depth modeling
- No probabilistic fills (v1)

---

## Deterministic Evaluation Cycle

For each historical bar T:

1. Bar T closes
2. Strategy evaluates
3. Signal emitted (if any)
4. OrderIntent created
5. BrokerOrder created (simulated)
6. Fill model executes (see fill-model.md)
7. Fill events emitted
8. PositionSnapshot updated
9. Metrics recorded

All transitions must emit recorded events.

---

## Research Engine Invariants

- No lookahead bias
- No future bar access
- Deterministic given identical inputs
- Fill timing explicitly defined
- All cost assumptions stored in RunManifest
- Every state transition recorded

---

## Non-Goals (v1)

- No intrabar simulation
- No microstructure modeling
- No queue position modeling
- No stochastic fill randomness