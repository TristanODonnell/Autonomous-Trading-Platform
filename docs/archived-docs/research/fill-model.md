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

## Order Rejection Modeling (F-02)

### Stochastic Rejection

When `order_rejection_probability` is set, each eligible order is subject to a
stochastic rejection draw before any order-type-specific logic runs.

Draw: `x = rng.random()` from the seeded simulation RNG.
If `x < order_rejection_probability` → order is **rejected**.

Rejected orders:
- Produce no fill
- Do not carry forward
- Are terminal for simulation purposes

This applies to both market and limit orders. The rejection draw uses the same seeded
`random.Random` instance as all other probabilistic features, preserving deterministic
replay.

`order_rejection_probability = None` (default) disables stochastic rejection entirely.

### Execution Order With Rejection

For each eligible order:

1. **Rejection check** (F-02) — if rejected: no fill, no carry-forward, stop.
2. **Limit eligibility** (R-07) — gap-open or intrabar touch check.
3. **Touch probability gate** (R-07) — intrabar touch acceptance/rejection.
4. **Volume participation cap** (R-04).
5. **Probabilistic partial fill** (F-01).
6. **Fill emission** — carry forward any remainder.

---

## DAY Limit Order Expiry (F-02)

### Default Behavior (Legacy)

By default (`expire_unfilled_limit_orders = False`), limit orders that are eligible but
do not fill on the current bar carry their full remaining quantity forward for
re-evaluation on the next bar. This applies to:

- Touch-probability rejections (R-07)
- Zero bar volume (participation cap yields 0)

### DAY Expiry Enabled

When `expire_unfilled_limit_orders = True`, limit orders that are eligible but unfilled
expire at end of bar instead of carrying forward:

| Scenario | Expiry disabled | Expiry enabled |
|----------|----------------|---------------|
| Not price-eligible (bar never reaches limit) | dropped | dropped (unchanged) |
| Touch-probability rejected | carry forward | expire |
| Zero bar volume (participation cap = 0) | carry forward | expire |
| Partial fill (some qty executes) | remainder carries forward | remainder carries forward |

Partial-fill remainders always carry forward regardless of the expiry flag — only the
portion that executed is complete; the unfilled portion needs rescheduling.

### Interaction With Rejection

Order rejection (F-02) fires before limit expiry logic. A rejected order never reaches
the expiry check and does not carry forward regardless of the expiry flag.

---

## Terminal State Guarantee

No order may remain in ambiguous state at backtest end.

All orders must end as:

- Filled
- Partially filled (remainder expired or carried to next bar)
- Rejected
- Expired
