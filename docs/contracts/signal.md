# Signal

## Purpose
- Canonical representation of a strategy decision at a specific evaluation window.
- Signals express *intent direction* only (not execution details).
- They serve as the boundary between strategy logic and execution logic.
## Producer / Consumer
- Produced by: Strategy Engine (Decision Layer)
- Consumed by:
  - Position Sizing / Portfolio Allocator
  - Risk Gate
  - OrderIntent Builder
  - Audit / Logging
## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `signal_id` | uuid | yes | Unique ID. |
| `run_id` | uuid | yes | Links to RunManifest. |
| `timestamp` | datetime (UTC) | yes | When the signal is emitted. |
| `bar_timestamp` | datetime (UTC) | yes | The MarketBar start time the strategy evaluated. |
| `strategy_id` | string | yes | Strategy identifier. |
| `symbol` | string | yes | Target symbol. |
| `direction` | enum | yes | `buy`, `sell`, `flat`. |
| `confidence` | float | no | 0..1 if used; else null. |
| `target_position` | float | no | Optional: desired position size (shares or %). |
| `params` | json | no | Strategy-specific details. |

## Invariants (Must Always Be True)
- `bar_timestamp` must align with MarketBar rules (5m boundary).
- If `direction="flat"`, then `target_position` (if present) must be 0.
- `confidence` ∈ [0,1] when present.
- For a given `(run_id, strategy_id, bar_timestamp, symbol)`, at most one Signal may exist.
## Validation Rules (Planning-Level)
- Check: missing `run_id`, `strategy_id`, `bar_timestamp` => halt (cannot audit).
- Check: unknown direction => reject signal.
- On failure: reject signal(s) and continue cycle unless failure is systemic (e.g., all signals invalid).

## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields are allowed (consumers must ignore unknown fields).
  - Any rename, removal, or semantic change requires `schema_version += 1`.
- Signals are immutable once emitted (append-only). Corrections require a new signal event.