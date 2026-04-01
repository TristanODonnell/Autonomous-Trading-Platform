# Layered Enablement Gates

## Objective
Ensure no single bug can route paper orders to live.

At least 4 independent gates must be satisfied before live execution is possible.

---

## Gate 1 — Build-Time Gate

Live trading code paths are excluded unless:

ENABLE_LIVE_BUILD = true

Default: false

If false:
- Live broker adapter not compiled
- Live credential loader not registered

This gate exists outside runtime configuration.

---

## Gate 2 — Configuration Gate

System default:

NO_LIVE_TRADING = true

To enable live:

- Explicit override required
- Config file must contain:
  environment: live
  allow_live_trading: true

Absence of both fields results in hard failure.

---

## Gate 3 — Runtime Human Confirmation Gate

Live execution requires:

- Short-lived live activation token
- Manually generated
- Time-limited (e.g., 15 minutes)
- Not stored in database

If token missing or expired → hard stop.

---

## Gate 4 — External Kill Switch

Out-of-band override mechanism.

Must:

- Exist outside main DB
- Exist outside primary service process
- Be checked before every order submission

If kill switch active:
- All broker submissions blocked
- System enters SAFE_MODE
- Outstanding orders canceled

Kill switch location example:
- Object storage key
- Separate microservice
- Environment variable injected by infra

---

## Safety Guarantee

Live routing requires:

Build Gate
AND Config Gate
AND Runtime Gate
AND Kill Switch Inactive

Failure of any gate blocks execution.
