# UniverseSnapshot

## Purpose
- Canonical, time-aware definition of the tradable universe used to:
  - constrain which symbols the strategy is allowed to trade
  - ensure backtests and live runs use identical membership rules (audit/repro)
  - prevent survivorship bias by preserving historical membership versions

## Producer / Consumer
- Produced by: Universe Builder (rules + data inputs) + Normalization Layer
- Consumed by:
  - Strategy Engine (symbol eligibility at each bar window)
  - Backtester (replays historical membership correctly)
  - Risk Engine (exposure caps per-universe / symbol allowlist)
  - Execution Gate (hard block if symbol not in active universe)
  - Audit/Repro (run replays and investigations)

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `universe_id` | string | yes | Deterministic ID, e.g. `"IEX_TOP500"`. |
| `snapshot_date` | date | yes | As-of date (rebalance date). |
| `effective_start` | datetime (UTC) | yes | When this membership becomes active. |
| `effective_end` | datetime (UTC) | no | When it stops being active (null = current). |
| `symbols` | list[string] | yes | Membership set. |
| `criteria` | json | yes | Filters used (ADV thresholds, price floor, etc.). |
| `version` | string | yes | Version tag (hash of symbols + criteria + snapshot_date). |
| `source` | string | yes | Provider/build method. |
| `built_at` | datetime (UTC) | yes | Build time (lineage). |
| `notes` | string | no | Human note. |


## Invariants (Must Always Be True)
- `symbols` is non-empty.
- No duplicate tickers inside `symbols`.
- `effective_start <= effective_end` if `effective_end` is set.
- `version` must be deterministic for the same `symbols+criteria+snapshot_date`.
- For a given `universe_id`, effective windows must not overlap.
- - At any evaluation timestamp, there must be **at most one** active snapshot for a given `universe_id`.

## Validation Rules (Planning-Level)
- Check: symbol format sanity (basic regex), reject obvious invalids.
- Check: universe too small (< N) => halt strategy cycle (universe integrity breach).
- On failure: do not “best-effort” trade a partial universe unless explicitly configured.

## Versioning
- `schema_version`: integer (start at `1`). Increment only when fields/types/invariants change.
- `version` (field in schema): deterministic membership version tag for this snapshot:
  - Recommended: hash of `(universe_id, snapshot_date, criteria, sorted(symbols))`.
  - This must be stable across rebuilds if inputs are identical.
- Run lineage:
  - Each run must record the universe membership version it used (e.g., `RunManifest.universe_version = UniverseSnapshot.version`).
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Breaking changes require `schema_version += 1`.
