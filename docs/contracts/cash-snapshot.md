# CashSnapshot

## Purpose
- Canonical representation of account cash state at a specific evaluation boundary.
- Provides the authoritative capital view used by the Risk Gate before order submission.
- Serves as the basis for exposure limits, buying power checks, and reconciliation.

## Producer / Consumer
- Produced by:
  - Ledger (primary source during runtime)
  - Reconciliation Layer (broker-aligned snapshot when available)
- Consumed by:
  - Risk Gate (capital checks)
  - Position Sizing
  - Monitoring / Alerts
  - Reporting

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `snapshot_id` | uuid | yes | Unique snapshot event. |
| `run_id` | uuid | yes | Run linkage. |
| `timestamp` | datetime (UTC) | yes | As-of time. |
| `currency` | string | yes | `"USD"` in v1. |
| `cash` | float | yes | Available cash (can be 0+). |
| `buying_power` | float | yes | Broker/ledger buying power. |
| `reserved_cash` | float | yes | Cash reserved for open orders. |
| `equity` | float | no | Optional: account equity if known. |
| `source` | enum | yes | `ledger` or `broker_reconciled`. |
| `capital_bucket` | float | no | Capital allocated to this run (if segregated from total account equity). |
## Invariants (Must Always Be True)
- `cash >= 0`, `buying_power >= 0`, `reserved_cash >= 0`.
- `reserved_cash <= cash + buying_power` (loose invariant; broker semantics vary).
- `timestamp` must correspond to a strategy evaluation boundary (e.g., bar close or run cycle boundary).
- If `equity` is present, it must satisfy:
  `equity ≈ cash + total_position_market_value` (within tolerance).


## Validation Rules (Planning-Level)
- Check: missing cash snapshot => halt trading (capital-protection requirement).
- Check: negative balances => freeze + alert.
- If `equity` is present, it must satisfy:
  `equity ≈ cash + total_position_market_value` (within tolerance).


## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Any rename, removal, or semantic change requires `schema_version += 1`.
- Snapshots are immutable once recorded (append-only event model).
