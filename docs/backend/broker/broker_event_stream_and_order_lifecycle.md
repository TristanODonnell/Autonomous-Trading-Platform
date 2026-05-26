# Broker Event Stream and Order Lifecycle (E-01 / E-02)

## Overview

The platform synchronizes broker order state via two complementary paths:

```
Broker Websocket Stream  ──►  BrokerStreamFillProcessor  ──►  fill / status update
                                        ▲
Polling Reconciliation   ──────────────┘  (same idempotent delta-qty path)
```

The websocket stream is the **primary near-real-time source**.
Polling reconciliation is the **backstop** for missed events, reconnect recovery, and cold-start synchronization.

Both paths converge in `BrokerStreamFillProcessor` / `OrderReconciliationService`, which use the same `BrokerOrderMapper.extract_incremental_fill()` delta-qty mechanism. A fill that arrives via both paths produces exactly one ledger entry — the second delivery has delta_qty = 0 and is silently discarded.

---

## Order Lifecycle States

```
NEW ──► PENDING_NEW ──► SUBMITTED ──► PARTIALLY_FILLED ──► FILLED
 │                          │               │
 │                          ├──────────────►├──► PENDING_CANCEL ──► CANCELED
 │                          │               │                   └──► FILLED (race)
 │                          ├──────────────►└──► EXPIRED
 └──────────────────────────└──────────────────► REJECTED
```

### State Definitions

| State | Meaning |
|-------|---------|
| `NEW` | Intent created locally, not yet acknowledged by broker |
| `PENDING_NEW` | Submit request in flight, broker acknowledgement not yet received |
| `SUBMITTED` | Broker confirmed receipt; order is live on exchange |
| `PARTIALLY_FILLED` | One or more fills received, quantity incomplete |
| `FILLED` | Fully executed — terminal |
| `PENDING_CANCEL` | Cancel request sent; broker confirmation pending |
| `CANCELED` | Cancellation confirmed — terminal |
| `REJECTED` | Broker rejected the order — terminal |
| `EXPIRED` | Order lapsed without execution (DAY limit at close, broker 404) — terminal |

### Valid Transitions

| From | Event | To |
|------|-------|----|
| NEW | PENDING_SUBMIT | PENDING_NEW |
| NEW | SUBMIT | SUBMITTED |
| NEW | REJECT | REJECTED |
| PENDING_NEW | SUBMIT | SUBMITTED |
| PENDING_NEW | REJECT | REJECTED |
| PENDING_NEW | EXPIRE | EXPIRED |
| SUBMITTED | PARTIAL_FILL | PARTIALLY_FILLED |
| SUBMITTED | FULL_FILL | FILLED |
| SUBMITTED | REQUEST_CANCEL | PENDING_CANCEL |
| SUBMITTED | CANCEL | CANCELED |
| SUBMITTED | REJECT | REJECTED |
| SUBMITTED | EXPIRE | EXPIRED |
| PARTIALLY_FILLED | PARTIAL_FILL | PARTIALLY_FILLED |
| PARTIALLY_FILLED | FULL_FILL | FILLED |
| PARTIALLY_FILLED | REQUEST_CANCEL | PENDING_CANCEL |
| PARTIALLY_FILLED | CANCEL | CANCELED |
| PARTIALLY_FILLED | EXPIRE | EXPIRED |
| PENDING_CANCEL | CANCEL | CANCELED |
| PENDING_CANCEL | FULL_FILL | FILLED |
| PENDING_CANCEL | PARTIAL_FILL | PARTIALLY_FILLED |
| PENDING_CANCEL | EXPIRE | EXPIRED |

Terminal states (FILLED, CANCELED, REJECTED, EXPIRED) accept no further transitions. Any attempt raises `InvalidOrderTransitionError`.

---

## E-01: Websocket Event Stream

### Components

**`AlpacaOrderStreamClient`** (`execution/clients/alpaca_order_stream_client.py`)

Wraps `alpaca-py TradingStream`. Connects, subscribes to trade updates, and delivers normalized event dicts to a callback. Each event dict includes a `stream_received_at` ISO timestamp injected at receive time.

**`BrokerEventStreamService`** (`execution/services/broker_event_stream_service.py`)

Manages the stream lifecycle:
- `start()` — connects and loops until `stop()` is called or the task is cancelled
- Reconnects with exponential backoff (default 1s → 60s)
- `stop()` signals shutdown; interrupts any in-progress backoff sleep
- Errors in the `on_event` callback are caught and logged; the stream stays alive

**`BrokerStreamFillProcessor`** (`execution/services/broker_stream_fill_processor.py`)

Processes individual stream events:
1. Normalizes the order payload via `BrokerOrderMapper.to_broker_order()`
2. Extracts incremental fill via delta-qty (`extract_incremental_fill`)
3. Tags any fill with `update_source = "stream"` and `stream_received_at`
4. Drives state machine transition via `OrderStateMachineService`
5. Returns a `StreamProcessingResult` — callers persist the fill and status update

### Idempotency

Fills are identified by delta-qty: `current_filled_qty − previous_filled_qty`. A fill already recorded by either path raises delta_qty to zero on the next delivery, producing no fill. This makes the stream/poll combination inherently idempotent without requiring deduplication keys.

Out-of-order events (delta_qty < 0) are detected, logged at CRITICAL, and silently discarded — tracked qty is preserved monotonically.

### Reconnect Behavior

1. Stream drops or raises → backoff sleep begins
2. `stop()` during backoff → `asyncio.wait_for` on the stop event interrupts sleep immediately
3. Reconnect attempts at 1s, 2s, 4s … up to 60s
4. Clean session exit (stream closes normally) → backoff resets to 1s

### Startup Synchronization

On cold start or reconnect, polling reconciliation runs first to synchronize state. The stream then takes over for real-time updates. Any fills missed during the reconnect window are picked up by the next polling cycle.

---

## E-02: Broker 404 and Expiry Handling

### Broker Order Not Found

`OrderReconciliationService.reconcile_order()` catches `httpx.HTTPStatusError` with status 404.

- If the order's current status is in `_EXPIRABLE_STATUSES` (PENDING_NEW, SUBMITTED, PARTIALLY_FILLED, PENDING_CANCEL): the order transitions to **EXPIRED** via `OrderEvent.EXPIRE`. Audit log records `reason: broker_order_not_found_404`.
- If the order is already terminal: the 404 is unexpected and re-raised for investigation.
- If the order is NEW (not yet reconcilable): re-raised.

This prevents stale SUBMITTED orders from looping indefinitely in reconciliation when the broker has silently dropped them (DAY limit orders, cleanup after market close).

### Broker `done_for_day` / `expired` Status

`BrokerOrderMapper._map_order_status()` maps both `expired` and `done_for_day` broker statuses to `OrderStatus.EXPIRED`. This covers DAY limit orders that expire at market close without a 404 occurring.

### Open Order Set Integrity

`BrokerOrderRepository.OPEN_STATUSES` and `TrackedOrderRepository.list_reconcilable_orders()` both include `PENDING_NEW` and `PENDING_CANCEL` as open/reconcilable states, ensuring these orders are included in polling cycles and kill-switch sweeps.

---

## Reconciliation Source Traceability

Every fill metadata dict records:

| Key | Value |
|-----|-------|
| `update_source` | `"stream"` or `"poll"` |
| `stream_received_at` | ISO timestamp when the websocket event arrived (stream path) |
| `broker_update_timestamp` | Broker's own `updated_at` timestamp |
| `received_at` | Local processing time |

State machine audit log entries record `update_source` and `broker_order_id` in their metadata, enabling full traceability of whether a transition was driven by stream or poll.
