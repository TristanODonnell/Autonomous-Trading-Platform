# Survivorship Bias Controls

## Objective
Ensure no look-ahead or survivorship leakage
in universe membership or data selection.

---

## Time-Aware UniverseSnapshot

UniverseSnapshot fields:
- snapshot_date
- symbols[]
- filter_parameters_hash
- dataset_version
- universe_version

---

## Critical Rule

When running backtest for date T:

Universe membership = UniverseSnapshot
where snapshot_date <= T
and closest preceding snapshot.

Never use "current universe" for historical runs.

---

## Question Guarantee

System must answer:

"Was symbol X eligible on date Y?"

This must be deterministic using:
- UniverseSnapshot table
- Snapshot history
- Lifecycle mapping

---

## Prohibited Behavior

- No retroactive addition of successful companies
- No removal of bankrupt/delisted symbols
- No dynamic filtering using future data
