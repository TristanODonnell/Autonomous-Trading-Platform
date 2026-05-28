# Domain: Strategy

## Overview

The strategy domain is responsible for generating trading decisions based on market data and managing the lifecycle of positions.

It operates on a bar-by-bar evaluation model (5-minute cadence) and produces Signals, which are then converted into OrderIntents for execution.

---

## Decision Flow

The strategy pipeline follows this progression:

Market Data → Strategy Evaluation → Signal → OrderIntent

- **Signal**: expresses directional intent (buy/sell/flat)
- **OrderIntent**: represents a concrete execution instruction after portfolio construction and sizing

Signals are generated at each evaluation boundary and passed to downstream systems for execution.

---

## Strategy Lifecycle

The intended lifecycle is:

IDLE → SIGNALLED → PENDING → IN_POSITION → EXIT_PENDING → COOLDOWN → IDLE

### State Meaning

- **IDLE**: no active position
- **SIGNALLED**: valid entry signal generated
- **PENDING**: order submitted but not yet filled
- **IN_POSITION**: position established (fill confirmed)
- **EXIT_PENDING**: exit order submitted
- **COOLDOWN**: post-exit waiting period before re-entry

### Invariants (Intended)

- Only one position per symbol (v1)
- Entry orders only allowed from IDLE
- Strategy state must reflect broker-confirmed position
- No transition to IN_POSITION without a confirmed fill

---

## Current Behavior

The current implementation provides a partially wired strategy system:

- Strategy evaluation produces Signals
- Portfolio construction generates OrderIntents from Signals
- Strategy state updates occur for:
  - SIGNAL_GENERATED → SIGNALLED
  - ORDER_INTENTS_CREATED → PENDING

State machine implementation exists and enforces valid transitions via exceptions.

However:

- Strategy transitions are not fully driven by broker outcomes
- Fill events do not reliably trigger transitions to IN_POSITION
- Rejections do not consistently transition to COOLDOWN
- Exit logic is not fully integrated with execution feedback

---

## Limitations

The current strategy system is a partial implementation of the intended lifecycle model.

Key limitations:

- Strategy state is not fully synchronized with broker-confirmed fills
- Partial fills do not trigger complete state transitions
- No automatic transition to COOLDOWN on rejection
- No enforcement of position ownership invariants at runtime
- Strategy lifecycle is not tightly coupled to reconciliation results
- Event naming and triggers differ from original specification

As a result, strategy behavior is structurally defined but not fully enforced operationally.
