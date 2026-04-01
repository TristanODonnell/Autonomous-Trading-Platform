# BrokerOrder

## Purpose
- Canonical representation of a broker-facing order and its lifecycle state.
- Tracks the authoritative execution status returned by the broker.
- Serves as the reconciliation anchor between internal OrderIntent and external execution events.
## Producer / Consumer
- Produced by:
  - Broker Adapter (paper/live mode)
  - Simulator (backtest mode)
- Consumed by:
  - Ledger (position & PnL updates)
  - Reconciliation Layer
  - Risk Monitoring
  - Audit / Reporting
## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `broker_order_id` | string | yes | Broker-assigned order ID (primary). |
| `client_order_id` | string | yes | Your deterministic client ID (used for idempotent submit). |
| `intent_id` | uuid | yes | Link to OrderIntent. |
| `run_id` | uuid | yes | Link to RunManifest. |
| `broker` | string | yes | `"alpaca"` for v1. |
| `account_id` | string | yes | Paper/live account id. |
| `symbol` | string | yes | Ticker. |
| `side` | enum | yes | `buy` / `sell`. |
| `order_type` | enum | yes | Mirrors intent. |
| `time_in_force` | enum | yes | Mirrors intent. |
| `extended_hours` | bool | yes | Mirrors intent. |
| `qty` | float | no | Requested shares. |
| `notional` | float | no | Requested notional. |
| `limit_price` | float | no | If applicable. |
| `stop_price` | float | no | If applicable. |
| `status` | enum | yes | `new`,`submitted`,`partially_filled`,`filled`,`canceled`,`rejected`. |
| `submitted_at` | datetime (UTC) | no | When accepted by broker. |
| `updated_at` | datetime (UTC) | yes | Last state update time. |
| `filled_qty` | float | yes | Cumulative filled quantity (0..qty). |
| `avg_fill_price` | float | no | Weighted average fill price if any fills occurred. |
| `last_error` | string | no | Rejection/cancel reason. |
| `raw_broker_payload` | json | no | Store original broker response for audit/debug. |
| `requested_qty` | float | no | Final share quantity submitted to broker (resolved from qty/notional). |
## Invariants (Must Always Be True)
- `client_order_id` is unique per run (or per account) and deterministic from the intent.
- State machine is monotonic (no illegal transitions):
  - `new -> submitted -> partially_filled -> filled`
  - `submitted/partially_filled -> canceled`
  - `new/submitted -> rejected`
- `filled_qty >= 0` and never decreases.
- If `status="filled"` then `filled_qty` equals requested qty (or broker-reported final).
- If `status="filled"` then `filled_qty > 0`.
- If `status="partially_filled"` then `0 < filled_qty < requested_qty`.
- Terminal states (`filled`, `canceled`, `rejected`) are immutable and cannot transition further.


## Validation Rules (Planning-Level)
- Check: broker_order_id missing after submit => halt + alert (cannot reconcile).
- Check: status transition illegal => quarantine event stream + freeze trading (serious bug).
- On failure: freeze trading + require manual intervention (order state corruption is high-risk).
- Check: fill events referencing unknown `broker_order_id` => quarantine + alert.


## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Any rename, removal, or semantic change requires `schema_version += 1`.
- BrokerOrder records are append-only; state transitions must be recorded as new updates, never silent overwrite.
