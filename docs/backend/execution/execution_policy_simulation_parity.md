# Execution Policy Simulation Parity (TASK D-01)

## Overview

Simulation now applies the same execution policy semantics as live/paper trading.

Before this change, `SimulationExecutionEngine` bypassed `ExecutionPolicyEngine` entirely.
Strategies configured for TWAP, VWAP-lite, or limit-offset execution were backtested as a
single immediate fill — a fundamental mismatch with live behavior.

After this change, simulation applies policy-aware execution through a shared model abstraction
that reuses the existing slicer and order-type-resolver logic without calling broker code.

---

## Architecture

### Shared Abstraction: IExecutionModel

`src/autonomous_trading_platform/execution/policy/i_execution_model.py`

```
IExecutionModel (Protocol)
  plan(intent, config, reference_price) → list[(bar_offset, child_intent)]
```

Separates execution policy semantics (shared) from runtime mechanics:

- **Simulation**: child intents flow through the existing simulation fill pipeline.
- **Live/Paper**: child intents submitted to the broker adapter (future).

### SimulatedExecutionModel

`src/autonomous_trading_platform/research/simulation/services/simulation_execution_model.py`

Simulation-side implementation. Never imports broker code. Reuses:

- `TWAPSlicer` — equal time-weighted quantity splits
- `VWAPLiteSlicer` — volume-profile-weighted quantity splits
- `OrderTypeResolver` — limit price computation from bar close as mid-price

Returns `list[(bar_offset, child_intent)]` where `bar_offset` is the number of bars
after the signal bar (plus `latency_bars`) at which the child should execute.

### ExecutionPlan Contract

`src/autonomous_trading_platform/contracts/execution/execution_plan.py`

```python
@dataclass(frozen=True)
class ChildOrderIntent:
    child_intent_id: UUID     # deterministic UUID5
    parent_intent_id: UUID    # traceability
    slice_index: int
    bar_offset: int
    intent: OrderIntent
    policy_mode: PolicyMode
```

---

## Policy Behavior in Simulation

### PASSTHROUGH

Returns `[(0, original_intent)]` per order. Identical to pre-policy simulation.
All existing backtests using default config are unaffected.

### MARKET

Forces `order_type=MARKET` on the child intent. Returns `[(0, child)]`.

### LIMIT with Offset

Computes `limit_price` from the bar close as reference price:

```
BUY  limit = close * (1 + offset_bps / 10_000)
SELL limit = close * (1 - offset_bps / 10_000)
```

Returns `[(0, limit_child)]`. The child limit order flows through the existing
R-07 limit-order realism logic (gap-open fills, intrabar touch probability).

Falls back gracefully to the original intent if no reference price is available.

### TWAP

Splits parent order into `num_slices` equal child intents scheduled over consecutive bars:

```
Parent:   BUY 1,000 shares
TWAP (5 slices):
  (bar_offset=0, BUY 200)
  (bar_offset=1, BUY 200)
  (bar_offset=2, BUY 200)
  (bar_offset=3, BUY 200)
  (bar_offset=4, BUY 200)
```

In simulation, `bar_offset=slice_index` maps the TWAP "time window" to discrete bars.
The last slice absorbs any remainder from integer division.

Child order type defaults to MARKET; can be LIMIT when `slice_order_type="limit"`.

### VWAP-lite

Same scheduling model as TWAP but child quantities are weighted by volume profile:

- `uniform`: equal weight per slice (equivalent to TWAP by quantity)
- `u_shaped`: higher weight at first and last slices (classic U-shaped intraday curve)

All VWAP-lite child orders are MARKET type.

---

## Parent-Child Traceability

All child intents carry full lineage metadata:

| Field | Source |
|-------|--------|
| `child_intent_id` | UUID5(`_CHILD_NS`, `f"{parent.intent_id}:{slice_index}:{policy_mode}"`) |
| `parent_intent_id` | `str(parent.intent_id)` embedded in `intent.metadata` |
| `slice_index` | embedded in `intent.metadata` |
| `policy_mode` | embedded in `intent.metadata` |
| `slice_quantity` | embedded in `intent.metadata` |
| `slice_weight` | embedded in `intent.metadata` |

Original parent metadata keys are preserved — child metadata is merged on top.

---

## Interaction with Existing Fill Pipeline

Child intents produced by `SimulatedExecutionModel` flow unchanged through:

- **Latency scheduling**: `bar_offset + latency_bars` determines the target bar index
- **Participation caps (R-04)**: `max_volume_participation_rate` applies per child fill
- **Probabilistic partial fills (F-01)**: `partial_fill_probability` applies per child
- **Limit-order realism (R-07)**: gap-open fills and touch-probability gate for LIMIT children
- **Order rejection (F-02)**: `order_rejection_probability` applies before any child fill logic
- **Carry-forward**: partially filled children carry their remainder to the next bar

No changes to `SimulatedExecutionService` were required.

---

## Determinism

All policy-simulation behavior is deterministic:

- Child intent IDs: UUID5 derived from parent intent ID + slice index + policy mode
- Slice quantities: deterministic from `TWAPSlicer` / `VWAPLiteSlicer` (no RNG)
- Fill IDs: existing P-01 mechanism unchanged
- Scheduling: `bar_offset` derived from `slice_index`, no wall-clock dependency

Same dataset + strategy + policy config + seed always produces identical:
- child intents
- slice quantities and bar offsets
- fill IDs and prices
- trade log
- equity curve

---

## Runtime Separation

| Layer | Code | Broker Dependency |
|-------|------|------------------|
| `SimulatedExecutionModel` | simulation path | None |
| `ExecutionPolicyEngine` | live/paper path | `AlpacaBrokerClient` (live quotes) |
| `SimulatedExecutionService` | simulation path | None |

`SimulatedExecutionModel` never imports `AlpacaBrokerClient`.
`ExecutionPolicyEngine` never imports `SimulatedExecutionService`.

---

## Integration Point

`SimulationExecutionEngine.execute()` accepts an optional `execution_policy_config`:

```python
engine.execute(
    run_id=run_id,
    strategy=strategy,
    window=window,
    context_builder=context_builder,
    simulated_execution_service=exec_svc,
    initial_cash=initial_cash,
    execution_policy_config=ExecutionPolicyConfig(
        policy_mode=PolicyMode.TWAP,
        twap=TWAPConfig(num_slices=5, window_minutes=25),
    ),
)
```

When `execution_policy_config=None` (default), behavior is identical to pre-policy simulation.

---

## Key Files

| File | Purpose |
|------|---------|
| `execution/policy/i_execution_model.py` | `IExecutionModel` Protocol |
| `research/simulation/services/simulation_execution_model.py` | `SimulatedExecutionModel` |
| `contracts/execution/execution_plan.py` | `ChildOrderIntent` dataclass |
| `research/simulation/services/simulation_execution_engine.py` | Engine integration |
| `tests/research/simulation/test_simulation_policy_execution.py` | 48 tests |
