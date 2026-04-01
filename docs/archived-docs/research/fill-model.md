# Fill Model

## Assumptions (v1)

- Liquidity available equal to reported bar volume
- No order book modeling
- No queue priority
- No hidden liquidity modeling
- No adverse selection modeling

---

## Market Orders

Filled at:

next_bar_open ± slippage

---

## Limit Orders

If:

limit_price ∈ [bar_low, bar_high]

Then:

fill at limit_price ± slippage

Else:

remain pending
cancel after 1 bar

---

## Partial Fill Handling

If order_qty > (bar_volume × max_participation_rate):

Fill:

bar_volume × max_participation_rate

Remaining:

Cancel after 1 bar (v1)

---

## Terminal State Guarantee

No order may remain in ambiguous state at backtest end.

All orders must end as:

- Filled
- Canceled
- Rejected
