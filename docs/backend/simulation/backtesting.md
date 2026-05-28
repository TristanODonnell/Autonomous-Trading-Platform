# Domain: Backtesting

## Overview

The backtesting domain is intended to provide deterministic simulation of strategy behavior over historical data.

It will support:

- bar-by-bar replay of historical datasets
- strategy evaluation under controlled conditions
- cost and fill modeling
- experiment reproducibility via RunManifest

---

## Current Status

This domain is not yet implemented.

There is no backtesting engine, fill model, or cost model integrated into the system.

---

## Planned Responsibilities

- Replay MarketBar datasets deterministically
- Apply strategy evaluation per bar
- Simulate order execution using fill models
- Track positions, cash, and risk over time
- Produce performance metrics and run artifacts

---

## Notes

Backtesting is planned as a future extension of the runtime and storage systems, leveraging existing contracts and RunManifest for reproducibility.
