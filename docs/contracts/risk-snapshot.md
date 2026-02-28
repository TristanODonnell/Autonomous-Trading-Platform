# RiskSnapshot

## Purpose
- Canonical representation of portfolio risk metrics at a specific evaluation boundary.
- Determines whether new OrderIntents are permitted to proceed.
- Serves as the enforcement boundary between portfolio state and execution.

## Producer / Consumer
- Produced by:
  - Risk Engine (evaluates positions + cash + limits)
- Consumed by:
  - Execution Gate (blocks or permits OrderIntent emission)
  - Monitoring / Alerting
  - Audit / Reporting

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `snapshot_id` | uuid | yes | Unique snapshot. |
| `run_id` | uuid | yes | Run linkage. |
| `timestamp` | datetime (UTC) | yes | As-of time (cycle boundary). |
| `gross_exposure` | float | yes | Sum(abs(position_value)). |
| `net_exposure` | float | yes | Sum(position_value). |
| `leverage` | float | yes | `gross_exposure / equity` (or / capital_bucket). |
| `drawdown_pct` | float | no | Optional in v1. |
| `limits` | json | yes | Configured risk limits (caps). |
| `utilization` | json | yes | Current usage vs limits. |
| `is_blocked` | bool | yes | Whether trading is blocked right now. |
| `block_reasons` | list[string] | no | Human-readable reasons. |
## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Breaking changes require `schema_version += 1`.
- RiskSnapshot records are immutable (append-only).
- Risk evaluation logic version must be recorded in RunManifest (`risk_engine_version` or part of `strategy_version`).

## Invariants (Must Always Be True)
- All exposure numbers are finite (no NaN/inf).
- `gross_exposure >= abs(net_exposure)`.
- If `is_blocked=true`, then `block_reasons` must be non-empty (auditability).
- `timestamp` must correspond to a strategy evaluation boundary.
- `leverage >= 0`.
- If equity (or capital_bucket) is zero, leverage must be defined as 0 and `is_blocked=true`.
- For each configured limit in `limits`, a corresponding utilization entry must exist in `utilization`.
- If any utilization exceeds its configured limit, `is_blocked` must be true.


## Validation Rules (Planning-Level)
- Check: if risk snapshot missing => default to `is_blocked=true`.
- Check: if any limit breached => `is_blocked=true` + record reasons.
- Check: if computed metrics are inconsistent with PositionSnapshot or CashSnapshot => freeze trading and alert.


## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Breaking changes require `schema_version += 1`.
- RiskSnapshot records are immutable (append-only).
- Risk evaluation logic version must be recorded in RunManifest (`risk_engine_version` or part of `strategy_version`).