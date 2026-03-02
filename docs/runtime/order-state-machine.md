# Order State Machine (v1)

Orders follow strict deterministic transitions.

States:

Created → Submitted → Partial → Filled
Created → Submitted → Rejected
Submitted → Canceled
Partial → Canceled

---

# State Definitions

Created:
    OrderIntent generated but not yet sent

Submitted:
    Broker accepted order request

Partial:
    One or more fills received, not complete

Filled:
    Fully executed

Canceled:
    Explicit cancellation confirmed

Rejected:
    Broker rejection

---

# Terminal States

Terminal:
- Filled
- Canceled
- Rejected

No transitions allowed after terminal state.

---

# Transition Triggers

Created → Submitted:
    Broker API 200 OK

Submitted → Partial:
    First fill event

Partial → Filled:
    Filled quantity == requested quantity

Submitted → Canceled:
    Cancel confirmed

Partial → Canceled:
    Cancel confirmed for remainder

Submitted → Rejected:
    Broker rejection response

---

# Retry Policy

Retries allowed only for:
- Network failure before submission confirmation

Retries forbidden for:
- Validation errors
- Risk gate failures
- Explicit rejections

Idempotency enforced via:

intent_id
run_id
strategy_id
idempotency_key

Duplicate submissions must be impossible.

## Forbidden Transitions (Explicit)

The following transitions are invalid and MUST NOT occur:

- Filled → Canceled
- Filled → Rejected
- Canceled → Filled
- Rejected → Submitted
- Rejected → Partial
- Rejected → Filled
- Created → Partial (must be Submitted first)

Any attempt to apply an invalid transition MUST:
- raise a runtime error
- emit ORDER_TRANSITION_INVALID
- freeze trading for the run

## Transition → Recorded Event Mapping

Every transition must emit exactly one ORDER_STATE_CHANGED event.

| From | To | Trigger | Recorded Event |
|------|----|---------|----------------|
| Created | Submitted | broker_submit_ok | ORDER_STATE_CHANGED |
| Submitted | Partial | broker_fill_event | ORDER_STATE_CHANGED |
| Partial | Filled | broker_fill_event_qty_complete | ORDER_STATE_CHANGED |
| Submitted | Canceled | broker_cancel_confirmed | ORDER_STATE_CHANGED |
| Partial | Canceled | broker_cancel_confirmed | ORDER_STATE_CHANGED |
| Submitted | Rejected | broker_rejection | ORDER_STATE_CHANGED |

