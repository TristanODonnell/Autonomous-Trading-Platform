# Hard Caps & Throttles Specification

All caps enforced pre-execution.

No OrderIntent may reach broker adapter before cap validation.

---

## Exposure Caps

- Max gross exposure
- Max net exposure
- Max per-symbol exposure
- Max leverage

Violation → reject OrderIntent

---

## Notional Caps

- Max daily notional traded
- Max per-order notional
- Max rolling 24h notional

---

## Order Rate Limits

- Max orders per bar
- Max orders per hour
- Max orders per day

Prevents runaway loops.

---

## Shadow Mode Contract

Shadow Mode = Execution disabled, logic active.

In shadow mode:

- Signals generated
- OrderIntents generated
- OrderIntents stored
- ZERO broker API calls allowed

Broker adapter must not initialize in shadow mode.

Shadow mode is mandatory for strategy validation before paper/live.

---

## Broker Account Allowlist

OrderIntent must include:

account_id

Account must be in allowlisted set:

ALLOWLIST = {paper_account_id, live_account_id}

If account_id not in allowlist:
- Hard fail
- Audit log entry required
