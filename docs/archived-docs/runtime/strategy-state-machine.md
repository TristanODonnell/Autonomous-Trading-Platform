# Strategy Lifecycle State Machine (v1)

States:

IDLE → SIGNALLED → PENDING → IN_POSITION → EXIT_PENDING → COOLDOWN → IDLE

---

# State Definitions

IDLE:
    No active position

SIGNALLED:
    Valid entry signal generated

PENDING:
    Entry order submitted

IN_POSITION:
    Entry fully filled

EXIT_PENDING:
    Exit order submitted

COOLDOWN:
    Exit filled; waiting before next entry

---

# Transition Triggers

IDLE → SIGNALLED:
    Valid signal event

SIGNALLED → PENDING:
    OrderIntent created

PENDING → IN_POSITION:
    Entry filled

IN_POSITION → EXIT_PENDING:
    Exit condition met

EXIT_PENDING → COOLDOWN:
    Exit filled

COOLDOWN → IDLE:
    Cooldown duration elapsed

---

# Position Ownership Invariants

1. Strategy may hold max one position per symbol (v1).
2. Entry orders only allowed in IDLE.
3. Exit orders only allowed in IN_POSITION.
4. State must match broker-confirmed position.
5. Strategy cannot transition to IN_POSITION without broker fill.

## Forbidden Strategy Transitions

Invalid transitions include:

- IDLE → IN_POSITION (must go through SIGNALLED → PENDING)
- SIGNALLED → IN_POSITION (requires broker fill)
- IN_POSITION → IDLE (must exit via EXIT_PENDING → COOLDOWN)
- EXIT_PENDING → IN_POSITION (exit cannot re-enter)
- Any transition out of COOLDOWN except COOLDOWN → IDLE

Invalid transitions MUST emit STRATEGY_TRANSITION_INVALID and freeze trading.

## Transition → Recorded Event Mapping

| From | To | Trigger | Recorded Event |
|------|----|---------|----------------|
| IDLE | SIGNALLED | signal_generated | STRATEGY_STATE_CHANGED |
| SIGNALLED | PENDING | order_intent_created | STRATEGY_STATE_CHANGED |
| PENDING | IN_POSITION | entry_order_filled | STRATEGY_STATE_CHANGED |
| IN_POSITION | EXIT_PENDING | exit_condition_met | STRATEGY_STATE_CHANGED |
| EXIT_PENDING | COOLDOWN | exit_order_filled | STRATEGY_STATE_CHANGED |
| COOLDOWN | IDLE | cooldown_elapsed | STRATEGY_STATE_CHANGED |
