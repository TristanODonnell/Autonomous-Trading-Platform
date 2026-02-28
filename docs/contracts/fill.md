# Fill

## Purpose
- Canonical representation of an execution event returned by the broker.
- Represents the atomic economic transaction that updates positions, cash, and P&L.
- Serves as the authoritative source of truth for portfolio state changes.

## Producer / Consumer
- Produced by:
  - Broker Adapter (paper/live mode)
  - Simulator (backtest mode)
- Consumed by:
  - Ledger (position + cash updates)
  - P&L Engine
  - Risk Engine
  - Reconciliation Layer
  - Audit / Reporting

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `fill_id` | string | yes | Unique fill identifier (broker exec id if available). |
| `broker_order_id` | string | yes | Links to BrokerOrder. |
| `intent_id` | uuid | yes | Links back to OrderIntent (audit). |
| `run_id` | uuid | yes | Links to RunManifest. |
| `timestamp` | datetime (UTC) | yes | Execution time. |
| `symbol` | string | yes | Ticker. |
| `side` | enum | yes | `buy` / `sell`. |
| `quantity` | float | yes | Executed quantity (>0). |
| `price` | float | yes | Execution price (>0). |
| `fees` | float | no | Total fees if provided. |
| `liquidity` | enum | no | Optional: `maker` / `taker` if known. |
| `venue` | string | no | Exchange/route if known. |
| `metadata` | json | no | Raw broker fields. |

## Invariants (Must Always Be True)
- A Fill must reference an existing `broker_order_id`.
- `quantity > 0` and `price > 0`.
- For a given `broker_order_id`, cumulative `quantity` must not exceed the broker-reported requested quantity (allow small epsilon for fractional rounding).
- `fill_id` must be globally unique per broker account (idempotent upsert key).
- `symbol` and `side` must match the referenced BrokerOrder.
- `timestamp` must be >= BrokerOrder.submitted_at.


## Validation Rules (Planning-Level)
- Check: fill arrives for unknown order => quarantine + alert (possible data loss / restart bug).
- Check: duplicates (same fill_id) => ignore (idempotent upsert).
- On failure: freeze trading only if systemic; otherwise quarantine the single fill.
- Check: if cumulative filled quantity exceeds requested quantity => freeze trading (execution corruption).

## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Any rename, removal, or semantic change requires `schema_version += 1`.
- Fill records are immutable once recorded (append-only).
- Duplicate `fill_id` events must be treated as idempotent replays.