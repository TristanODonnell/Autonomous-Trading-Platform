# Audit Log (v1) — Immutable Event Log Spec

## Purpose

The audit log is an append-only, immutable event stream that records:
- run and step lifecycle transitions
- order lifecycle transitions (internal + broker)
- reconciliation results (including mismatches and freeze actions)

It exists to ensure the platform is:
- auditable
- reproducible (trace what happened, when, and why)
- debuggable under incident conditions

This directly supports the platform’s design philosophy of reproducibility and auditability. :contentReference[oaicite:2]{index=2}

---

## Non-Negotiables (v1)

1. Append-only: events are never updated or deleted.
2. Total ordering: events must be orderable within a run.
3. Correlation: every event must be linkable to a `run_id`, and when applicable, to an `order_id`, `intent_id`, or `step_id`.
4. Integrity: events must be tamper-evident (at minimum via hash chaining per run).
5. Sufficient detail: events must contain enough context to explain system behavior without requiring additional hidden state.

---

## Storage Model

### Logical Table / Stream
`audit_events` (conceptual; implementation could be a Postgres table or a write-ahead log persisted to storage)

### Required Fields (Canonical Event Envelope)

| Field | Type | Required | Notes |
|------|------|----------|------|
| event_id | uuid | Yes | Unique per event |
| run_id | uuid | Yes | Parent run |
| event_time_utc | datetime | Yes | When event occurred (system time) |
| event_type | string | Yes | Namespaced type (see Event Types) |
| entity_type | string | Yes | e.g., `run`, `step`, `order`, `reconciliation` |
| entity_id | string | Yes | ID for referenced entity (order_id, step_id, etc.) |
| severity | enum | Yes | `info`, `warn`, `error`, `critical` |
| sequence_num | int | Yes | Monotonic per run_id (or per entity stream) |
| correlation_id | string | No | Cross-service correlation |
| actor | string | No | `system`, `strategy:<id>`, `broker_adapter`, `operator` |
| payload | json | Yes | Event-specific data |
| prev_event_hash | string | No | Hash chaining (per run stream) |
| event_hash | string | No | Hash of envelope+payload |

### Ordering Rule
Within a run, `sequence_num` MUST be strictly increasing. This provides deterministic replay and debugging.

---

## Event Type Taxonomy (v1 Minimal)

### 1) Run Lifecycle Events
- `run.created`
- `run.started`
- `run.succeeded`
- `run.failed`
- `run.canceled`

Minimum payload:
- `run_manifest_id` (or embedded manifest pointer)
- `mode` (`backtest|paper|live`)
- `reason` (for failure/cancel)

### 2) Step Lifecycle Events
Steps are meaningful units of work (ingest, evaluate, risk-check, place-orders, reconcile).

- `step.started`
- `step.succeeded`
- `step.failed`
- `step.skipped`

Minimum payload:
- `step_name`
- `inputs` (dataset_version_ids, universe_version_id if relevant)
- `duration_ms`
- `error` (if failed)

### 3) Order Lifecycle Events (Internal)
These represent state transitions in the platform’s internal order state machine.

- `order_intent.created`
- `order.created`
- `order.submitted`
- `order.canceled`
- `order.rejected`
- `order.filled`
- `order.partially_filled`
- `order.error`

Minimum payload:
- `intent_id` (when applicable)
- `idempotency_key`
- `symbol`, `side`, `qty/notional`
- `order_type`, `limit_price`, `tif`, `extended_hours`
- `state_from`, `state_to`
- `reason` (reject/cancel/error)
- `broker_order_id` (once known)

### 4) Broker Order Events (External)
These are raw or normalized broker lifecycle signals (WebSocket updates, REST status polls).

- `broker_order_event.received`

Minimum payload:
- `broker` (e.g., `alpaca`)
- `broker_order_id`
- `status`
- `raw_payload` (or normalized fields + optional raw)

Note: You may also treat these as your dedicated `broker_order_events` table; the audit log can either duplicate minimally (pointer + summary) or store them fully. The key requirement is that broker lifecycle transitions are auditable.

### 5) Reconciliation Events
Reconciliation ensures internal ledger/positions match broker-reported truth. The platform requires freezing trading on mismatch. :contentReference[oaicite:3]{index=3}

- `reconciliation.started`
- `reconciliation.completed`
- `reconciliation.mismatch_detected`
- `reconciliation.freeze_triggered`
- `reconciliation.resolved`

Minimum payload:
- `as_of_time_utc`
- `scope` (`positions|cash|orders|fills`)
- `broker_snapshot_ref` (pointer to broker response or stored snapshot)
- `internal_snapshot_ref`
- `diff_summary` (counts, notional deltas)
- `action_taken` (e.g., `freeze_trading`, `halt_run`)
- `requires_manual_ack` (bool)

---

## Minimal Payload Schemas (Examples)

### Example: `step.failed`
```json
{
  "step_name": "evaluate_strategy",
  "inputs": {
    "dataset_version_id": "bars_adj_5m@...",
    "universe_version_id": "univ@..."
  },
  "duration_ms": 421,
  "error": {
    "type": "MissingBarData",
    "message": "Bars missing for 12 symbols at 2026-02-27T15:35:00Z"
  }
}
```
### Example: `order.submitted`
```json
{
  "intent_id": "intent-uuid",
  "order_id": "order-uuid",
  "idempotency_key": "run:<run_id>:bar:<ts>:symbol:AAPL:side:buy",
  "symbol": "AAPL",
  "side": "buy",
  "qty": 10,
  "order_type": "limit",
  "limit_price": 187.32,
  "time_in_force": "day",
  "extended_hours": false,
  "state_from": "created",
  "state_to": "submitted",
  "broker_order_id": "alpaca-..."
}
```

### Example: `reconciliation.mismatch_detected`
```json
{
  "as_of_time_utc": "2026-02-27T15:40:00Z",
  "scope": "positions",
  "diff_summary": {
    "symbols_mismatched": 2,
    "total_notional_delta_usd": 531.12
  },
  "action_taken": "freeze_trading",
  "requires_manual_ack": true
}
```

## Tamper Evidence (v1)
### Minimum requirement:
- Hash chain per run: each event stores prev_event_hash, and event_hash is computed from (envelope + payload + prev hash).
- This is sufficient for detecting post-hoc mutation of the event stream.

## Retention (v1)
- Retain audit log indefinitely for v1 development.
- Before any live trading, define a retention policy suitable for compliance use-cases (multi-year). The planning doc explicitly calls for long retention.

## Acceptance Criteria
### The audit log must support:

1. Run traceability:
- reconstruct run lifecycle, step outcomes, and failure causes

2. Order traceability:
- reconstruct each order intent, submission, broker response, fills, and terminal state

3. Reconciliation traceability:
- prove reconciliation occurred, document mismatches, and document freeze actions taken

4. Reproducibility linkage:
- link audit events to RunManifest, DatasetVersion, and UniverseVersion so the run can be replayed exactly.
