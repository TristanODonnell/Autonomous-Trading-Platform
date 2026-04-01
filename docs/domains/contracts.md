# Domain: Contracts

## Overview

Contracts define the canonical data objects exchanged across ingestion, strategy, execution, risk, storage, and orchestration.

They provide a shared schema layer so that all parts of the platform operate on the same object model during backtests, paper runs, and future live execution.

The contract layer currently centers around market data, strategy outputs, execution objects, state snapshots, and run metadata.

## Contract Groups

### Market & Reference Data

- **MarketBar**: canonical 5-minute OHLCV input used by strategy evaluation, valuation, and backtesting.
- **CorporateAction**: normalized split/dividend/merger/name-change records used for adjusted data handling and continuity.
- **UniverseSnapshot**: time-aware membership definition used to constrain which symbols are eligible at a given timestamp.

### Decision & Execution Contracts

The execution pipeline follows this progression:

`Signal -> OrderIntent -> BrokerOrder -> Fill`

- **Signal**: strategy decision output at a bar boundary.
- **OrderIntent**: internal, risk-approved execution instruction.
- **BrokerOrder**: broker-facing order object and lifecycle state.
- **Fill**: normalized execution event used for ledger and portfolio updates.
- **CostBreakdown**:
- **SlippageMeasurement**:

### State & Risk Snapshots

- **PositionSnapshot**: holdings at an evaluation boundary.
- **CashSnapshot**: cash and buying-power state at an evaluation boundary.
- **RiskSnapshot**: risk metrics and block status used to gate execution.

### Run & Reproducibility Metadata

- **RunManifest**: root record describing run configuration, environment, strategy version, and data references.
- **UniverseSnapshot / universe_version usage**: links evaluation and backtests to the exact membership definition used during the run.
- **AuditLog**:
- **TickerLifecycleEvent**:

## Cross-Cutting Invariants

Across the contract layer, the intended design relies on a few common rules:

- timestamps are UTC and aligned to evaluation/data boundaries where applicable
- contracts used for replay and audit should be immutable once recorded
- run-linked contracts must be traceable via `run_id`
- strategy and execution objects should be reproducible from manifest + input data
- snapshot-style objects should represent point-in-time state, not mutable rolling state

## Current Implementation Notes

The codebase currently implements many of these contracts as Pydantic models, and basic validation exists for several of them.

Implemented examples include:
- MarketBar alignment and basic OHLC/volume rules
- OrderIntent sizing validation (`qty` XOR `notional`)
- basic non-negative and uniqueness checks for PositionSnapshot and CashSnapshot
- basic exposure and block-reason checks for RiskSnapshot

## Limitations

The current implementation does not fully enforce all originally documented contract guarantees.

Known gaps include:
- many contracts omit explicit `schema_version` handling
- append-only semantics are not consistently enforced in repositories
- deterministic idempotency/client ID behavior is not fully implemented across execution contracts
- BrokerOrder/Fill lifecycle constraints are only partially enforced
- UniverseSnapshot determinism differs from the original spec
- RunManifest immutability is not strictly enforced
