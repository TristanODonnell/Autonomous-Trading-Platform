# Backtest Semantics

## Simulation Model (v1)

Bar-based with event-like order lifecycle.

Orders are evaluated at bar close and filled using the next bar's data.

---

## Fill Timing Rule (Locked)

Orders generated at bar close T
are eligible to fill at bar T+1.

This prevents lookahead bias.

---

## Order Lifecycle (Simulated)

Created
→ Submitted
→ Partial (optional)
→ Filled (terminal)
→ Canceled (terminal)
→ Rejected (terminal)

Each transition must:

- Have a trigger
- Emit an event
- Be recorded in the event log

---

## Partial Fill Rules

Define liquidity participation:

participation_rate = order_qty / bar_volume

If participation_rate ≤ max_participation_threshold:
    Fill fully

If participation_rate > threshold:
    Fill partial equal to:
        bar_volume × threshold

Remaining quantity:

- Remains pending
- Cancel after N bars (v1: N = 1)

---

## Limit Order Semantics

If limit_price ∈ [low, high] of next bar:

    Fill at limit_price ± slippage

Else:

    Remain pending
    Cancel after 1 bar

---

## Market Order Semantics

Fill at:

next_bar_open ± slippage_adjustment

---

## Cancellation Rules

Orders auto-cancel after:

- 1 bar (v1 default)
- End-of-backtest

All cancellations emit events.
