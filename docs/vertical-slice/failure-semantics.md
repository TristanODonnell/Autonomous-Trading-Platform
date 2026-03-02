# Failure Semantics — Phase 8

## Missing Data Behavior

If required indicators cannot compute due to missing bars:

- Skip evaluation for symbol
- Emit DataSkipEvent
- Do not generate OrderIntent

No forward-fill for signal generation.

---

## Late Data (SLA Breach)

If evaluation cycle skipped due to SLA:

- No strategy evaluation
- No state transitions
- Log incident

---

## Broker Rejection

If BrokerOrder rejected:

- Emit RejectionEvent
- Strategy transitions to COOLDOWN
- No retry in same bar

---

## Partial Fill Handling

If partial fill occurs:

- Position size updated incrementally
- Remaining quantity canceled after 1 bar
- Strategy transitions to IN_POSITION once any fill > 0

---

## Capital Breach

If execution would exceed:

- Max position cap
- Max total capital cap

Then:

- OrderIntent not generated
- RiskRejectionEvent emitted

---

## Kill Switch Activation

If kill switch triggered:

- Cancel all open orders
- Liquidate positions
- Transition strategy to IDLE
- Emit EmergencyLiquidationEvent