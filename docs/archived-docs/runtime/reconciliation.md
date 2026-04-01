# Reconciliation Model (v1)

Reconciliation runs:

- Every evaluation cycle (before strategy evaluation)
- End-of-day
- On system restart

---

# Reconciliation Scope

Compare:

- Internal positions vs broker positions
- Internal open orders vs broker open orders
- Cash balance vs broker reported cash
- Buying power

---

# Mismatch Handling

If mismatch detected:

→ Freeze trading immediately
→ Cancel outstanding orders
→ Emit CRITICAL alert
→ Require human acknowledgment
→ Manual resolution required

Trading cannot resume without:

- Explicit human approval
- Verified state alignment

---

# Reconciliation Invariants

1. Internal position quantity must equal broker quantity.
2. No internal order may exist without broker counterpart.
3. No broker open order may exist without internal tracking.
4. Cash delta must be explainable by fills or fees.

Any invariant violation triggers freeze.

## Reconciliation Events (Required)

Reconciliation MUST emit:

- RECONCILIATION_STARTED
- RECONCILIATION_PASSED
- RECONCILIATION_FAILED (with mismatch details)

On failure it MUST also emit:

- TRADING_FROZEN (reason=reconciliation_mismatch)
- HUMAN_ACK_REQUIRED

## Human Acknowledgment Semantics

Human acknowledgment is required to resume after a freeze.

A human ack is a recorded event:

- HUMAN_ACK_GRANTED (actor, timestamp, reason, reference)

Until HUMAN_ACK_GRANTED is recorded:
- no evaluation may create OrderIntents
- no broker calls are permitted
