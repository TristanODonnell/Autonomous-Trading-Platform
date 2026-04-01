# Domain: Safety

## Overview

The safety system is responsible for preventing unintended capital deployment and enforcing constraints before any broker interaction occurs.

It provides:

- environment isolation (paper vs live)
- pre-execution validation (idempotency, caps, allowlists)
- execution gating (live enablement controls)
- duplicate prevention
- shadow mode for safe validation

All OrderIntents are expected to pass safety checks before reaching execution.

---

## Environment Model

The system supports multiple execution environments:

- paper
- live
- shadow (non-executing mode)

Current behavior:

- Environment is configured via runtime settings
- Separate account IDs are defined for paper and live environments
- Shadow mode disables broker submission

Intended guarantees:

- No mixing of environments
- No accidental routing from paper → live

---

## Execution Gating (Live Enablement)

The intended design uses multiple gates to prevent unintended live trading:

- build-time gate
- configuration gate
- runtime human confirmation
- external kill switch

Current behavior:

- A LiveTradingGateService exists to coordinate gating checks
- It validates:
  - environment configuration
  - account allowlist
  - runtime gate state
  - kill switch state

Limitations:

- Gates are only checked at the start of a trading cycle
- Not enforced at order submission level
- Runtime gate and kill switch are in-memory only
- No persistent or external kill-switch mechanism

---

## Order Safety Checks

Before execution, OrderIntents are expected to pass several validations.

### Idempotency

- Deterministic idempotency keys are generated using:
  - run_id, strategy_id, bar_timestamp, symbol, side, quantity
- Duplicate detection logic exists

Limitations:

- No persistent idempotency store
- Uses stub reader → duplicates not actually prevented across runs

---

### Caps & Throttles

The system includes:

- OrderThrottleService:
  - per-bar limits
  - per-hour limits
- PreTradeRiskService:
  - gross exposure checks
  - per-symbol exposure checks
  - daily notional limits

Limitations:

- Net exposure and leverage not enforced
- Per-order and rolling notional caps missing
- Uses stub risk data → caps effectively not enforced

---

### Broker Account Allowlist

- Account allowlists are defined via configuration
- Validation logic exists to check:
  - account_id ∈ allowlist(environment)

Limitations:

- Allowlist enforced only in live-gate flow
- Not consistently enforced before order submission
- Stored in runtime config (not external system)

---

## Shadow Mode

Shadow mode is a non-executing mode used for validating strategy logic.

Current behavior:

- Strategy evaluation runs normally
- OrderIntents are generated
- Broker submission is skipped

Limitations:

- No explicit audit labeling for shadow outputs
- Risk snapshots not persisted
- No enforcement of "no broker initialization" beyond conditional checks

---

## Current Behavior

The safety system is partially implemented and integrated:

- Idempotency keys are generated deterministically
- Basic throttle and exposure checks exist
- Shadow mode suppresses broker calls
- Live trading gate logic exists but is not fully integrated

However:

- Many safety checks rely on stubbed data sources
- Gating is not enforced consistently at execution boundaries
- Some invariants are implemented structurally but not operationally enforced

---

## Limitations

The current safety system is a partial implementation of the intended design.

Key limitations:

- Live trading gates not enforced at order submission
- Kill switch is in-memory (not external or persistent)
- Idempotency not backed by durable storage
- Caps rely on stub data → not enforced in practice
- Allowlist not consistently applied across execution flow
- No enforcement of full invariant chain before broker interaction
- Scheduler does not integrate safety checks at all required stages
