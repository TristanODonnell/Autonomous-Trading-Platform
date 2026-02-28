# PositionSnapshot

## Purpose
- Snapshots capture the system state at evaluation time.
- **PositionSnapshot:** lists current holdings 
- (`symbol`, `quantity`, `average_cost`, `market_value`).

## Producer / Consumer
- Produced by: Ledger
- Consumed by: Risk Gate + Reporting + Reconciliation

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `snapshot_id` | uuid | yes | Unique snapshot event. |
| `run_id` | uuid | yes | Run linkage. |
| `timestamp` | datetime (UTC) | yes | As-of time (evaluation boundary). |
| `positions` | list[json] | yes | Array of per-symbol position objects (below). |
| `source` | enum | yes | `ledger` or `broker_reconciled`. |

### Position object (inside `positions`)
| Field | Type | Required | Description |
|---|---|---:|---|
| `symbol` | string | yes | Ticker. |
| `quantity` | float | yes | Signed quantity (v1: long-only => >=0). |
| `avg_cost` | float | no | Average cost basis. |
| `market_price` | float | no | Last price used for valuation (from MarketBar close). |
| `market_value` | float | no | `quantity * market_price`. |
| `unrealized_pnl` | float | no | Optional in v1. |

## Invariants (Must Always Be True)
- Unique `symbol` within `positions`.
- If v1 long-only: `quantity >= 0` for all symbols.
- `timestamp` must match evaluation cadence (bar close boundary or cycle boundary).

## Validation Rules (Planning-Level)
- Check: missing snapshot at cycle boundary => halt trading (risk gate cannot run blind).
- Check: negative qty in long-only mode => freeze + alert.

## Versioning
- schema_version: 1
- Compatibility: (rules)