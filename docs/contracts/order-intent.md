# OrderIntent

## Purpose
- Canonical, risk-approved instruction describing a specific order to be submitted to the broker.
- Represents the transition from strategy intent to executable trade.
- Serves as the idempotent boundary between internal decision logic and external broker interaction.
## Producer / Consumer
- Produced by:
  - Position Sizing / Portfolio Allocator
  - Risk Gate (after approval)
- Consumed by:
  - Broker Adapter (paper/live)
  - Simulator (backtest mode)
  - Audit / Logging
  - Reconciliation Layer

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `intent_id` | uuid | yes | Primary ID (generated once). |
| `idempotency_key` | string | yes | Deterministic dedupe key (see invariants). |
| `run_id` | uuid | yes | Links to RunManifest. |
| `strategy_id` | string | yes | Who created it. |
| `timestamp` | datetime (UTC) | yes | Intent creation time. |
| `bar_timestamp` | datetime (UTC) | yes | The bar window that triggered this intent. |
| `symbol` | string | yes | Ticker. |
| `side` | enum | yes | `buy` / `sell`. |
| `qty` | float | no | Share quantity. |
| `notional` | float | no | Dollar amount if using fractional notionals. |
| `order_type` | enum | yes | `market`, `limit`, `stop`, `stop_limit`. |
| `limit_price` | float | no | Required for limit/stop_limit. |
| `stop_price` | float | no | Required for stop/stop_limit. |
| `time_in_force` | enum | yes | `day`, `gtc`, `opg`, `cls`, `ioc`, `fok`. |
| `extended_hours` | bool | yes | Execution eligibility outside RTH. |
| `client_order_id` | string | yes | Deterministic ID passed to broker adapter. |
| `metadata` | json | no | Correlation IDs, reasoning, etc. |

## Invariants (Must Always Be True)
- **Idempotency:** `(run_id, strategy_id, bar_timestamp, symbol, side, order_type, limit_price, stop_price, qty, notional, time_in_force, extended_hours)` deterministically maps to `idempotency_key`.
- **Exactly one sizing mode:** (`qty` XOR `notional`) — one must be set, not both.
- If `order_type="limit"` => `limit_price` is required.
- If `order_type="stop"` => `stop_price` is required.
- If `order_type="stop_limit"` => both `stop_price` and `limit_price` required.
- `qty > 0` if set; `notional > 0` if set.
- `client_order_id` must be deterministically derived from `idempotency_key`.
- For a given `idempotency_key`, at most one OrderIntent may exist (append-only).
- `bar_timestamp` must correspond to an existing MarketBar within the same `run_id`.

## Validation Rules (Planning-Level)
- Check: violates XOR sizing or missing required prices => reject intent.
- Check: if extended-hours is true and order_type != limit => reject (v1 policy).
- On failure: reject + alert; do not “fix” orders silently.
- Check: if order exceeds configured risk limits => block and log reason (do not emit OrderIntent).

## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields are allowed (consumers must ignore unknown fields).
  - Any rename, removal, or semantic change requires `schema_version += 1`.
- OrderIntent records are immutable once created (append-only).
- Retries must reuse the same `idempotency_key` and `client_order_id`.
