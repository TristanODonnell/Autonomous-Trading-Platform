# Order State Machine (archived — superseded by E-01/E-02)

> This document describes the **original v1 state machine** before E-01/E-02 hardening.
> The canonical reference for the current state machine is
> `docs/architecture/broker_event_stream_and_order_lifecycle.md`.

---

## v1 States

```
Created → Submitted → Partial → Filled
Created → Submitted → Rejected
Submitted → Canceled
Partial → Canceled
```

**Terminal states:** Filled, Canceled, Rejected.

## What Changed in E-01/E-02

| Area | v1 | v2 (current) |
|------|----|--------------|
| States | 6 (Created, Submitted, Partial, Filled, Canceled, Rejected) | 9 (+PENDING_NEW, +PENDING_CANCEL, +EXPIRED) |
| Fill discovery | Polling-only | Websocket stream primary, polling backstop |
| Expiry handling | Stale SUBMITTED forever | Broker 404 → EXPIRED; `done_for_day` → EXPIRED |
| Cancel flow | Submitted → Canceled (direct) | Submitted → PENDING_CANCEL → Canceled |
| Submission flow | New → Submitted (direct) | New → PENDING_NEW → Submitted (optional) |
| Duplicate fills | Not explicitly guarded | Delta-qty idempotency on both stream + poll paths |
