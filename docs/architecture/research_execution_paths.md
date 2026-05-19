# Research / Replay / Backtest Execution Path Audit

**Date:** 2026-05-18
**Status:** Authoritative classification — supersedes the informal path descriptions in `research_strategy_audit.md §1`

See also: `docs/architecture/research_strategy_audit.md` for strategy-layer and experiment-funnel gaps.

---

## TL;DR for Developers

| Question | Answer |
|---|---|
| Which path should I use for strategy research? | `research/simulation/simulation_runner.py` via `research run-experiment` or `research run-simulation` |
| Which path should I use for high-fidelity historical replay? | `scheduler/backtest/backtest_trading_cycle_orchestrator.py` |
| Which path runs the full data → features → experiment golden path? | `scheduler/orchestration/historical_research_golden_path_orchestrator.py` |
| Which path should I use to test risk-gate wiring? | `runtime/replay_debug.py` (RuntimeReplayDebugRunner) |
| Which paths should not receive new features? | `backtest_replay_orchestrator.py` (legacy), `replay_debug.py` / `replay_runtime_service.py` (debug tooling only) |

---

## Path Classification Table

| Path | Classification | Entry Points | Real Strategy? | Feature Pipeline? | Fills | Persistence |
|---|---|---|---|---|---|---|
| `research/simulation/simulation_runner.py` | **canonical_research** | CLI `research run-simulation`, `ExperimentOrchestrationService`, `PipelineRunner` | Yes | Yes (via dataset version) | Simulated (close ± slippage) | Parquet artifacts + SOR metadata |
| `scheduler/orchestration/historical_research_golden_path_orchestrator.py` | **canonical_research** (orchestration wrapper) | Scheduler DAG, `run_experiment_pipeline_cycle` | Yes (via experiment_plan) | Yes (backfill → corp-action → features) | Simulated | Dataset versions + runtime jobs |
| `scheduler/cycles/run_experiment_pipeline_cycle.py` | **canonical_research** (scheduler entrypoint) | Airflow DAG, CLI `research run-experiment` | Yes | Yes | Simulated | Runtime jobs + governance state |
| `scheduler/backtest/backtest_trading_cycle_orchestrator.py` | **canonical_replay** | Direct instantiation (CLI `backtesting run` is `not_implemented`) | Yes (real trading evaluation job) | Yes (backfill + feature pipeline) | Simulated via BacktestBrokerClient | Full SOR lifecycle (fills, snapshots, risk) |
| `runtime/replay_debug.py` (RuntimeReplayDebugRunner) | **integration_replay** / **debug_demo** | Direct instantiation, tests | No (synthetic deterministic signals) | No | Simulated (synthetic prices) | SOR with `DEBUG_REPLAY` markers |
| `runtime/services/replay_runtime_service.py` (ReplayRuntimeService) | **integration_replay** | Direct instantiation, tests | No (same as above) | No | Simulated (synthetic prices) | SOR (non-debug job naming) |
| `scheduler/backtest/backtest_replay_orchestrator.py` | **legacy** | Direct instantiation (not exposed via CLI) | No (hard-coded MA crossover) | No | Simulated (close ± slippage) | SOR fills + snapshots |

---

## Path Descriptions

### 1. `research/simulation/simulation_runner.py`
**Classification: canonical_research**

The canonical path for single-strategy research simulation. Resolves a dataset version, loads a bounded
bar/feature window from Parquet, instantiates a strategy via `StrategyFactory`, runs `SimulationExecutionEngine`
tick-by-tick, computes return/risk/trade/stability metrics, records Parquet artifacts and SOR metadata.

Used by:
- `ExperimentOrchestrationService.run_experiment()` for sweep/AB/rolling-window/staged experiments
- `PipelineRunner` for staged multi-filter experiments
- CLI `research run-simulation` (direct call, no experiment wrapping)
- CLI `research run-experiment` (via orchestration layer)

**Use this path for:** any strategy research, parameter sweeps, multi-stage filtering, experiment persistence.

---

### 2. `scheduler/orchestration/historical_research_golden_path_orchestrator.py`
**Classification: canonical_research (orchestration layer)**

Top-level scheduler orchestration for the full historical research workflow:

```
Backfill raw bars → Corporate action ingestion → Feature pipeline → Experiment pipeline (optional)
```

Wraps real Alpaca backfill (or configured source), corporate action adjustment, and
`run_experiment_pipeline_cycle()`. All stages recorded as RuntimeJobRuns.

**Use this path for:** full end-to-end research runs initiated from the scheduler or Airflow.

---

### 3. `scheduler/cycles/run_experiment_pipeline_cycle.py`
**Classification: canonical_research (scheduler entrypoint)**

Scheduler-level entry point for the experiment pipeline stage. Accepts an `ExperimentDefinition`
object or a YAML config path. Calls `ExperimentOrchestrationService` and seeds `StrategyGovernance`
rows for survivors. Can be invoked standalone or as the final stage of the historical golden path.

**Use this path for:** experiment pipeline scheduling or Airflow DAG integration.

---

### 4. `scheduler/backtest/backtest_trading_cycle_orchestrator.py`
**Classification: canonical_replay**

Full-fidelity historical backtest that mirrors the live trading pipeline. The only substitution
vs live trading is broker order submission: instead of calling Alpaca, `BacktestBrokerClient`
fills orders instantly at the bar close price ± slippage.

Pipeline:
```
run_market_backfill_cycle → run_feature_pipeline_cycle → [per bar: run_trading_evaluation_job → BacktestFillSimulator → snapshots → run_risk_snapshot_job]
```

Uses real strategy evaluation (`run_trading_evaluation_job`), real feature pipeline, real risk
snapshots. Writes fills, position/cash snapshots, risk snapshots, and runtime jobs to SOR.

**Use this path for:** high-fidelity backtesting where you need real strategy + feature + risk
pipeline semantics with synthetic fills.

**Note:** The CLI `backtesting run` command registers this intent but is `not_implemented` as of
2026-05-18. Instantiate `BacktestTradingCycleOrchestrator` directly until wired.

---

### 5. `runtime/replay_debug.py` — `RuntimeReplayDebugRunner`
**Classification: integration_replay / debug_demo**

A development and testing tool for verifying risk gate wiring, settings snapshot loading,
cycle handler behavior, and SOR write paths. Uses **synthetic prices** (random seed-based fallback)
and **deterministic buy/sell logic** (not real strategy implementations). All SOR rows are marked
with `DEBUG_REPLAY` metadata.

Seven risk gate layers are exercised: kill-switch, trading-enabled, portfolio drawdown,
risk tolerance multiplier, portfolio vol targeting, per-strategy drawdown, allocation override cap.

**Do NOT use this path for:** strategy research, parameter sweeps, or any result that should
inform production decisions. This path does not use real strategies or real bar data.

**Use this path for:** verifying risk parameters are wired correctly (`backtesting verify-risk-parameter-effects`),
testing cycle handler behavior, integration tests of the runtime replay infrastructure.

---

### 6. `runtime/services/replay_runtime_service.py` — `ReplayRuntimeService`
**Classification: integration_replay**

A thin wrapper over `RuntimeReplayDebugRunner` with `job_name_prefix="runtime_replay"` instead of
`"runtime_replay_debug"`. Semantically identical to the debug runner but with non-debug job naming,
intended for formally recorded replay evidence (e.g., in CI or scheduled verification pipelines).

**Same limitations as `RuntimeReplayDebugRunner`**: synthetic prices, synthetic strategy logic.

---

### 7. `scheduler/backtest/backtest_replay_orchestrator.py`
**Classification: legacy**

A simple SOR population tool that walks historical bars and generates MA-crossover signals with
hard-coded logic (no strategy factory, no feature pipeline, no risk gates). Originally used to
populate the SOR with realistic-looking fills and snapshots for UI development and dashboard
testing.

Signal logic is inline (`_MASignal`) and does not use any real strategy implementation.

**Do NOT add new features to this path.** It exists to seed the SOR for UI/dashboard testing only.
If you need a real MA crossover backtest, use `canonical_replay` or `canonical_research`.

---

## Entry Point Map

```
CLI: research run-simulation
  └─► SimulationRunner (canonical_research)
       └─► SimulationExecutionEngine
            └─► StrategyFactory → real strategy → SimulatedExecutionService

CLI: research run-experiment
  └─► ExperimentOrchestrationService (canonical_research)
       └─► [expands configs] → SimulationRunner × N → FilterScoreService
       └─► OR: PipelineRunner → [stage × N → SimulationRunner × M → filter]

CLI: backtesting verify-risk-parameter-effects / verify-notification-events / verify-governance-allocation
  └─► RuntimeReplayDebugRunner (integration_replay / debug_demo)

Airflow DAG / Scheduler:
  └─► HistoricalResearchGoldenPathOrchestrator (canonical_research orchestration)
       └─► run_market_backfill_cycle
       └─► run_corporate_action_ingestion_cycle
       └─► run_feature_pipeline_cycle
       └─► run_experiment_pipeline_cycle → ExperimentOrchestrationService

Direct instantiation (no CLI wrapper yet):
  └─► BacktestTradingCycleOrchestrator (canonical_replay)
       └─► run_market_backfill_cycle
       └─► run_feature_pipeline_cycle
       └─► [per bar] run_trading_evaluation_job → BacktestBrokerClient → snapshots → run_risk_snapshot_job
```

---

## Fill Semantics Comparison

All paths use **simulated fills** — no real broker orders are submitted. The differences:

| Path | Fill Price | Fill Timing | Cost Model | Source Prices |
|---|---|---|---|---|
| SimulationRunner / Engine | Close (CURRENT_CLOSE policy) | Immediate | Commission + slippage model | Parquet dataset |
| BacktestTradingCycleOrchestrator | Close ± slippage | Immediate | Config-based fee/slippage rate | Backfilled bars |
| RuntimeReplayDebugRunner | Synthetic tick price | Immediate | None | Synthetic (random seed fallback) or MarketBar |
| BacktestReplayOrchestrator | Close ± slippage | Immediate | Config-based fee/slippage rate | MarketBarRepository |

---

## Persistence Behavior Comparison

| Path | SOR Fills | SOR Snapshots | SOR Risk | SOR Jobs | Parquet Artifacts | Debug Markers |
|---|---|---|---|---|---|---|
| SimulationRunner | No | No | No | Yes | Yes (trade logs, equity curve, metrics, positions) | No |
| BacktestTradingCycleOrchestrator | Yes | Yes | Yes | Yes | No | No |
| RuntimeReplayDebugRunner | Yes | Yes | No | Yes | Optional JSON summary | Yes (`DEBUG_REPLAY`) |
| ReplayRuntimeService | Yes | Yes | No | Yes | Optional JSON summary | No |
| BacktestReplayOrchestrator | Yes | Yes | No | No | No | No |

---

## Confirmed Classification of Hypothesis

The task's starting hypothesis was:

> - `research/simulation/simulation_runner.py` → canonical research simulation path ✅ **CONFIRMED**
> - `scheduler/backtest/backtest_trading_cycle_orchestrator.py` → canonical high-fidelity replay ✅ **CONFIRMED**
> - `scheduler/backtest/backtest_replay_orchestrator.py` → legacy/demo ✅ **CONFIRMED**
> - `runtime/replay_debug.py` and `replay_runtime_service.py` → debug/replay tooling, not canonical research ✅ **CONFIRMED**

---

## Guardrails Added

1. **`backtest_replay_orchestrator.py`** — Module-level `CLASSIFICATION` block added explicitly labeling it `legacy` and prohibiting new features.
2. **`runtime/replay_debug.py`** — Module-level block added documenting `integration_replay / debug_demo` classification and synthetic-price limitation.
3. **`runtime/services/replay_runtime_service.py`** — Module docstring updated with classification.
4. **CLI `backtesting` help text** — `seed-fixture`, `run`, and `inspect-results` commands retain existing help. The `verify-*` commands already have accurate descriptions.

---

## Test Coverage of Each Path

| Path | Test Files | Coverage Level |
|---|---|---|
| SimulationRunner / Engine | `tests/research/simulation/test_lookahead_guard.py`, `test_determinism_seed.py`, `test_simulated_execution_service.py`, `test_simulator_fill.py`, `test_simulation_cost_model_service.py` | Partial (component-level; no full engine integration test) |
| ExperimentOrchestrationService + PipelineRunner | `tests/scheduler/test_experiment_pipeline_cycle.py` (indirect) | Partial (scheduler dispatch; no direct orchestration test) |
| HistoricalResearchGoldenPathOrchestrator | `tests/scheduler/test_historical_research_golden_path.py` | Good (full 4-stage pipeline integration test) |
| BacktestTradingCycleOrchestrator | None found | **Missing** |
| RuntimeReplayDebugRunner | `tests/runtime/test_runtime_replay_debug.py`, `test_risk_parameter_wiring.py` | Good |
| ReplayRuntimeService | `tests/runtime/test_replay_runtime_service.py` | Good |
| BacktestReplayOrchestrator | None found | **Missing** (acceptable for legacy path) |

---

## Deferred Work (TASK-0.2 and Later)

### TASK-0.2 — Wire `backtesting run` CLI to `BacktestTradingCycleOrchestrator`

The `backtesting run` command handler is `not_implemented`. It should be wired to
`BacktestTradingCycleOrchestrator` with appropriate CLI flags (symbols, dates, initial capital,
strategy fixture, output options).

### TASK-0.3 — Add Integration Test for `BacktestTradingCycleOrchestrator`

No test covers the canonical_replay path end-to-end. A test with a mock backfill cycle,
synthetic feature data, and real strategy evaluation would provide coverage and prevent regressions.

### TASK-0.4 — Resolve `run-simulation` Persistence Ambiguity

The CLI help text for `research run-simulation` says "bypasses orchestration entirely" and
implies no DB writes, but `SimulationRunner` still receives SOR repositories from
`build_simulation_context()` and records run metadata. Either:
- Make `run-simulation --no-persist` a real flag that skips recording, or
- Update the help text to accurately describe what is recorded.

### TASK-0.5 — Deprecate or Delete `BacktestReplayOrchestrator`

Once the canonical_replay path (`BacktestTradingCycleOrchestrator`) is wired to the CLI,
`BacktestReplayOrchestrator` can be removed unless there is a specific need for the lightweight
SOR population use case. If retained, it should be renamed to `SorPopulationReplayOrchestrator`
or similar to signal its purpose.

### TASK-0.6 — Add SOR Artifact Coverage to SimulationRunner

`SimulationRunner` writes Parquet artifacts but not SOR fills or position snapshots. This means
research equity curves are not visible in the Portfolio/Dashboard pages. Once `canonical_replay`
is the authoritative path for SOR-backed backtests, decide whether `SimulationRunner` should
optionally emit SOR snapshots for research visibility.
