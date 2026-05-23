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

### Gap-Open Fill Logic (R-07)

If the bar opens through the limit price, the order fills at `bar.open` (price improvement):

- **Buy:** `bar.open <= limit_price` → fill price = `bar.open`
- **Sell:** `bar.open >= limit_price` → fill price = `bar.open`

Gap-open fills bypass queue-position uncertainty — the market opened through the limit so
the order was at the front of the queue.

### Intrabar Touch Fill Logic (R-07)

If the bar does not open through the limit but the bar range reaches it:

- **Buy:** `bar.open > limit_price` and `bar.low <= limit_price` → conditionally fillable at `limit_price`
- **Sell:** `bar.open < limit_price` and `bar.high >= limit_price` → conditionally fillable at `limit_price`

**Queue uncertainty (`fill_probability_on_touch`):**

When `fill_probability_on_touch` is set, a seeded RNG draw gates each intrabar touch:

- Draw `x` from `Uniform(0, 1)` using the simulation's seeded RNG.
- If `x < fill_probability_on_touch` → fill proceeds.
- If `x >= fill_probability_on_touch` → order is NOT filled; full quantity carries forward.

`fill_probability_on_touch = None` (default) disables the gate: every price touch fills
deterministically (legacy behavior, preserves R-04/F-01 compatibility).

An exact touch (`bar.low == limit_price` for a buy, `bar.high == limit_price` for a sell)
is treated as an intrabar touch and is subject to the same probability gate.

### Interaction with Participation Cap and Probabilistic Fills

Execution order for limit orders:

1. Determine eligibility (gap-open or intrabar touch).
2. If intrabar touch and `fill_probability_on_touch` is set: apply RNG gate.
3. If gate rejects: carry forward full quantity; skip steps 4–5.
4. Apply volume participation cap (R-04).
5. Apply probabilistic partial-fill reduction (F-01).
6. Emit fill at resolved price; carry forward remainder.

### Deterministic Replay

The same seeded `random.Random` instance used by F-01 probabilistic partial fills is
also used for `fill_probability_on_touch` draws. Providing identical seeds and inputs
produces identical limit-fill decisions across simulation re-runs.

### Not Eligible

If the bar never reaches the limit price, the order is silently dropped for that bar.
The strategy re-evaluates on the next bar.

---

### Legacy behavior (pre-R-07)

Prior to R-07, limit orders filled whenever `bar.low <= limit_price` (buy) or
`bar.high >= limit_price` (sell), always at `limit_price`, with no gap-open price
improvement and no touch-probability gate. This behavior is preserved when
`fill_probability_on_touch=None` and the bar does not gap through the limit.

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
