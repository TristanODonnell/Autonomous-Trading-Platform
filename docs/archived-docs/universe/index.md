# Universe Governance (v1) — Index

## Purpose

This folder locks universe selection, lifecycle handling, and survivorship controls for the trading platform.

The universe layer guarantees:

- Time-aware membership snapshots
- Deterministic symbol eligibility
- No survivorship bias
- Reproducible historical backtests
- Stable vertical slice definition for v1

Universe membership must never depend on future information.

These rules are binding for v1 implementation.

---

## Canonical References

- [v1 Universe Specification](v1-universe-spec.md)
  Locked universe definition, eligibility filters, exclusions, and rebalance cadence.

- [Universe Invariants](universe-invariants.md)
  Versioning rules, RunManifest requirements, and reproducibility guarantees.

- [Survivorship Bias Controls](survivorship-controls.md)
  Time-aware UniverseSnapshot rules and strict prohibition of lookahead leakage.

- [Universe Lifecycle Handling](universe-lifecycle.md)
  Symbol mapping, mergers, delistings, ticker changes, and continuity guarantees.

---

## Universe Guarantees (v1)

The universe system must guarantee:

- UniverseSnapshot is immutable once stored
- Each snapshot is versioned
- Historical runs use membership as-of snapshot_date
- RunManifest references:
  - universe_version
  - snapshot_date
- Symbol lifecycle events do not mutate historical bars
- Membership selection is a pure function of:
  - dataset version
  - filter configuration
  - snapshot date

The system must be able to answer deterministically:

> "Was symbol X eligible on date Y?"

---

## Snapshot Model (v1)

## Pages

- [v1 Universe Spec](v1-universe-spec.md)
- [Universe Invariants](universe-invariants.md)
- [Survivorship Controls](survivorship-controls.md)
- [Universe Lifecycle](universe-lifecycle.md)



Universe membership is defined by:

- snapshot_date
- symbols[]
- filter_parameters_hash
- dataset_version
- universe_version

When running a backtest for date T:

Universe membership =
closest preceding UniverseSnapshot
where snapshot_date ≤ T

No use of “current universe” is permitted for historical runs.

---

## Non-Goals (v1)

- No dynamic intraday universe updates
- No multi-universe experimentation
- No adaptive filtering
- No post-hoc membership edits
- No survivorship-based rebalancing

Universe governance focuses strictly on determinism, reproducibility, and historical integrity.
