# Research Engine (v1) — Index

## Pages

- [Research Engine Overview](research-engine-overview.md)
- [Backtest Semantics](backtest-semantics.md)
- [Fill Model](fill-model.md)
- [Cost Model](cost-model.md)
- [Experiment Tracking](experiment-tracking.md)
- [Stress Test Spec](stress-test-spec.md)


## Purpose

This folder locks the deterministic research engine specification used for backtesting and scenario analysis.

The research engine MUST share identical data contracts and structural behavior with paper and live trading.

Research is not allowed to invent alternate execution semantics or data shapes.

These rules are binding for v1 implementation.

---

## Canonical References

- [Research Engine Overview](research-engine-overview.md)
  Core architecture, deterministic evaluation cycle, invariants, and structural equivalence requirements.

- [Backtest Semantics](backtest-semantics.md)
  Bar-based simulation model, order lifecycle, fill timing rules, and cancellation behavior.

- [Fill Model](fill-model.md)
  Market and limit order assumptions, partial fill handling, and terminal state guarantees.

- [Cost Model](cost-model.md)
  Linear cost model, slippage mechanics, and determinism requirements.

- [Experiment Tracking](experiment-tracking.md)
  Required output artifacts, run manifest structure, metrics, and reproducibility guarantees.

- [Stress Test Specification](stress-test-spec.md)
  Mandatory stress scenarios and acceptance criteria for robustness validation.

---

## Research Engine Guarantees (v1)

The research engine must guarantee:

- No lookahead bias
- Deterministic evaluation given identical inputs
- Explicit fill timing rules
- Fully parameterized cost and fill models
- Identical structural contracts to paper/live
- Complete event logging for every state transition
- Reproducible outputs across identical runs

Structural equivalence requirement:

Signal
→ OrderIntent
→ BrokerOrder (simulated)
→ Fill events
→ Position updates
→ Reports

No research-only shortcuts are permitted.

---

## Non-Goals (v1)

- No intrabar or tick-level simulation
- No order book depth modeling
- No queue position modeling
- No stochastic/probabilistic fills
- No microstructure modeling
- No performance optimization benchmarking

Research focuses strictly on correctness, determinism, and structural equivalence with execution environments.
