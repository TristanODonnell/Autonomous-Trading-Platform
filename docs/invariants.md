# Global Invariants (v1)

## Purpose
These invariants must hold in every environment. Violations are treated as faults and must halt execution (or downgrade to shadow mode).

---

## Environment & Safety
1. Default mode is NO_LIVE_TRADING.
2. Paper environment cannot route orders to live accounts.
3. Broker account must be allowlisted or execution is blocked.
4. External kill switch overrides all other decisions.

## Data Integrity
5. MarketBar timestamps are aligned to 5-minute boundaries.
6. No duplicate (symbol, timestamp) MarketBars in a dataset version.
7. UniverseSnapshot is time-aware (no survivorship leakage).

## Execution Semantics
8. OrderIntent must include an idempotency key.
9. Duplicate OrderIntents (same key) must not create duplicate broker submissions.
10. Order state machine transitions are monotonic (no illegal backwards transitions).

## Reconciliation
11. If reconciliation mismatch exists, trading is frozen.
12. Reconciliation must be performed on a defined schedule and logged.

## Reproducibility
13. Every run must have a RunManifest containing dataset version + universe version + config snapshot + git commit.