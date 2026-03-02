# Phase 8 — v1 Strategy Specification

## Strategy Name

SMA_Cross_v1 (Single Strategy Baseline)

---

## Interval

5-minute bars

Evaluation occurs strictly at bar close.

---

## Entry Logic (Bar-Based)

Let:

- fast_ma = 20-period simple moving average
- slow_ma = 50-period simple moving average

Long Entry Condition:

fast_ma crosses above slow_ma
AND no open position for symbol
AND risk gate passes

Exit Condition:

fast_ma crosses below slow_ma

No shorting in v1.

---

## Holding Assumptions

- Strategy is directional long-only.
- One position per symbol.
- No pyramiding.
- Position closed only by:
  - Opposite signal
  - Risk breach
  - Forced liquidation (system event)

---

## Position Sizing (v1)

Per position:

min( 
    capital_bucket × 0.05,
    per_symbol_cap
)

Constraints:

- Max concurrent positions: 5
- Max total capital deployed: 50% of capital bucket

---

## Risk Constraints

- No margin
- No leverage
- No short selling
- No position > 10% of capital bucket
- No symbol traded outside UniverseSnapshot

---

## Signal Conflict Handling

If:

Signal = BUY
BUT risk gate blocks (capital exhausted or max positions reached)

Then:

- Signal is recorded
- No OrderIntent generated
- RiskRejectionEvent emitted

No retry allowed.

---

## Repeated Flip Handling

If:

Signal flips BUY → SELL → BUY within N bars (v1: N = 3)

Then:

- Suppress re-entry
- Enter COOLDOWN state for 5 bars

Prevents churn.

---

## Stop-Loss Policy (v1)

Stop-loss orders are disabled.

Replacement mechanism:

- Hard position sizing
- Max exposure cap
- Strategy exit via crossover
- Kill switch at system level

Stop-loss may be introduced in v2.