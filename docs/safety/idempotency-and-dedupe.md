# Idempotency & Duplicate Prevention

## Objective
Prevent duplicate submissions caused by retries, race conditions, or crashes.

---

## Idempotency Key Strategy

Idempotency key must be deterministic:

idempotency_key =
hash(
    run_id,
    strategy_id,
    bar_timestamp,
    symbol,
    side,
    target_qty
)

Given identical inputs, identical key must be produced.

---

## Duplicate Policy

If idempotency_key already exists:

- Within same run_id
- Within configurable time window (e.g., 5 minutes)

Second submission must be rejected.

---

## Persistence Requirement

Idempotency keys must be stored in system-of-record before broker submission.

Crash recovery must consult idempotency store before retrying.

---

## Safety Rationale

Duplicate prevention protects against:

- Infinite retry loops
- Broker timeouts
- Network retries
- Double order submission

Idempotency enforcement occurs before cap validation and before broker routing.