# Execution Constraints — Phase 8

## Supported Order Types (v1)

- Market
- Limit

No stop, stop-limit, OPG, CLS, IOC, or FOK.

---

## Market Orders

Used for:

- Strategy entries
- Strategy exits

Filled according to fill-model.md semantics.

---

## Limit Orders

Optional for:

- Entry confirmation improvement
- Extended-hours trading

Default: Market orders only.

---

## Extended-Hours Behavior

v1 Default: OFF

Strategy trades only during:

09:30 – 16:00 ET (regular session)

If extended-hours is enabled in future:

- Limit orders only
- Explicit configuration flag required

---

## Time-In-Force

DAY only.

No GTC in v1.

---

## Idempotency

Each OrderIntent must:

- Contain intent_id
- Be unique per evaluation cycle
- Prevent duplicate submissions

---

## Fractional Trading

Allowed.

Qty may be fractional but must:

- Respect notional caps
- Respect broker minimums