# Data Contracts Index (v1)

## Pages

- [MarketBar](marketbar.md)
- [Signal](signal.md)
- [OrderIntent](order-intent.md)
- [BrokerOrder](broker-order.md)
- [Fill](fill.md)
- [PositionSnapshot](position-snapshot.md)
- [CashSnapshot](cash-snapshot.md)
- [RiskSnapshot](risk-snapshot.md)
- [CorporateAction](corporate-action.md)
- [UniverseSnapshot](universe-snapshot.md)
- [RunManifest](run-manifest.md)

## Purpose
This index defines the canonical data objects used across ingestion, execution, and simulation.
All pipelines must share these contracts to preserve reproducibility and alignment between backtest and paper execution.

---

## Contracts (v1 Required)

### MarketBar
- Produced by: Data Ingestion Pipeline
- Consumed by: Decision/Execution + Scenario Analysis
- Purpose: Canonical 5-minute OHLCV bar input.

### CorporateAction
- Produced by: Data Ingestion Pipeline
- Consumed by: Adjustment logic + Validation + Scenario Analysis
- Purpose: Splits/dividends/mergers continuity and price/position adjustments.

### UniverseSnapshot
- Produced by: Universe Governance (Ingestion layer)
- Consumed by: Strategy + Simulation
- Purpose: Time-aware universe membership (survivorship-safe).

### Signal
- Produced by: Strategy Evaluation
- Consumed by: Portfolio/Risk Gate
- Purpose: Strategy decision output before risk/portfolio mapping.

### OrderIntent
- Produced by: Decision/Risk Gate
- Consumed by: Broker Adapter + Audit
- Purpose: Intended action (idempotent), pre-execution representation.

### BrokerOrder
- Produced by: Broker Adapter
- Consumed by: Ledger + Reconciliation
- Purpose: Broker-level order representation + state machine.

### Fill
- Produced by: Broker Adapter / Simulator
- Consumed by: Ledger + Reporting
- Purpose: Executed fills (partial/full).

### PositionSnapshot
- Produced by: Ledger
- Consumed by: Risk Gate + Reporting + Reconciliation
- Purpose: Current holdings, valuation, ownership state.

### CashSnapshot
- Produced by: Ledger
- Consumed by: Risk Gate + Reporting + Reconciliation
- Purpose: Cash/buying power accounting.

### RiskSnapshot
- Produced by: Risk Engine
- Consumed by: Execution gate + Monitoring
- Purpose: Exposure/drawdown/limits state used to gate orders.

### RunManifest
- Produced by: Orchestrator / Run bootstrap
- Consumed by: Everything (audit/repro)
- Purpose: Immutable record of configuration + dataset/universe versions + environment.
