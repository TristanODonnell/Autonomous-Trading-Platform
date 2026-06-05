# Portfolio CLI Domain Audit: `portfolio.py`

Target CLI domain: `portfolio`

Target CLI file: `src/autonomous_trading_platform/cli/commands/portfolio.py`

Audit status: target file does not exist yet. This audit records the empty current inventory and the proposed portfolio-domain entrypoints needed for portfolio state inspection, API-equivalent read models, allocation resolution, construction artifacts, and simulation allocation verification.

Domain definition: `portfolio` owns portfolio state and portfolio construction: summary, holdings, equity curve, performance, allocation views, allocation policy resolution, allocation configuration snapshots for deterministic simulation, construction pipeline artifacts, signal netting/conflict diagnostics, and local portfolio-state verification. It should not own manual allocation override mutation (`controls`), capital/trading risk constraints (`risk`), broker order placement (`execution`), scheduler orchestration (`runtime`), REST/frontend smoke checks (`api`), or full workflow bundles (`platform`).

## 1. Current CLI Inventory

No commands are registered in `src/autonomous_trading_platform/cli/commands/portfolio.py` because the file does not exist. The `portfolio` domain is also not registered in `src/autonomous_trading_platform/cli/main.py`.

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp portfolio` | none | none | no | no | no | PLACEHOLDER |

Portfolio-like commands currently live elsewhere:

| Existing command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp backtesting read-portfolio` | none | `handle_read_portfolio` | no | no | yes | READ_ONLY_SAFE |
| REST `/api/v1/portfolio/summary` | n/a | `get_portfolio_summary` | no | conditional | conditional | BROKER_OR_EXTERNAL |
| REST `/api/v1/portfolio/holdings` | n/a | `get_portfolio_holdings` | no | conditional | conditional | BROKER_OR_EXTERNAL |
| REST `/api/v1/portfolio/allocation` | n/a | `get_portfolio_allocation` | no | conditional | conditional | BROKER_OR_EXTERNAL |
| REST `/api/v1/portfolio/equity-curve` | `period` | `get_portfolio_equity_curve` | no | no | yes | READ_ONLY_SAFE |
| REST `/api/v1/portfolio/performance` | `from_date`, `to_date` | `get_portfolio_performance` | no | no | yes | READ_ONLY_SAFE |
| REST `/api/v1/portfolio/risk` | n/a | `get_portfolio_risk` | no | no | yes | READ_ONLY_SAFE |
| REST `/api/v1/portfolio/performance/by-period` | n/a | `get_portfolio_performance_by_period` | no | no | yes | READ_ONLY_SAFE |
| REST `/api/v1/portfolio/construction/runs` and children | `limit`, `run_id`, `batch_id`, `constraint_status` | portfolio construction route handlers | no | no | yes | READ_ONLY_SAFE |

## 2. Domain Responsibility Check

| Command | Classification | Correct domain | Notes |
|---|---|---|---|
| `atp portfolio` | correctly placed, missing | portfolio | The domain belongs in the final CLI taxonomy but has not been implemented. |
| `atp backtesting read-portfolio` | should move to another domain | portfolio | It is API-equivalent portfolio state inspection, not backtesting-specific. |
| REST portfolio summary/holdings/allocation | correctly placed for REST; should be wrapped by CLI | portfolio | CLI should expose local DB-backed variants and optional broker-inclusive variants with explicit flags. |
| REST portfolio factor exposure/neutralization views | should be duplicated/wrapped elsewhere | portfolio and risk | Portfolio can expose portfolio-facing read views; risk should own factor-risk computation and thresholds. |
| Portfolio construction artifact routes | correctly placed | portfolio | Construction pipeline artifacts are portfolio-domain read models. |
| `StrategyAllocationService.override_allocation` via strategies route | should be duplicated/wrapped elsewhere | controls | Manual override mutation belongs to controls; portfolio should preview/resolve allocation, not mutate overrides. |
| `run_allocation_rebalance_cycle` | should be duplicated/wrapped elsewhere | runtime/governance/portfolio | Portfolio can inspect allocation/rebalance results; scheduler execution belongs to runtime and governance. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp portfolio snapshot --json` | Print API-equivalent summary, holdings, allocation, risk, performance, and 1m equity curve. | Direct replacement for `backtesting read-portfolio`. | read-only | `PortfolioSummaryService`, `PortfolioAnalyticsService`, `PortfolioEquityCurveService` | P0 |
| `atp portfolio summary --json` | Print current portfolio value, PnL, and cash balance. | Core portfolio read model. | read-only | `PortfolioSummaryService.get_summary` | P0 |
| `atp portfolio holdings --json` | List current holdings from latest position snapshot. | Core portfolio state inspection. | read-only | `PortfolioAnalyticsService.get_holdings` | P0 |
| `atp portfolio allocation --json` | Show by-strategy and by-asset allocation view. | Portfolio allocation read model. | read-only | `PortfolioAnalyticsService.get_allocation`, fallback equivalent to route `_allocation_from_strategy_config` | P0 |
| `atp portfolio allocation-config snapshot --output artifacts/portfolio/allocation-config.json` | Serialize current capital allocation policies and overrides with deterministic hash for simulation. | Recent allocation config capability needs a CLI verification/artifact surface. | read-only artifact output | `snapshot_allocation_config` | P0 |
| `atp portfolio allocation-config show --json` | Print allocation config snapshot and hash without writing. | Lets users verify behavior-affecting allocation config. | read-only | `snapshot_allocation_config` | P0 |
| `atp portfolio allocation-config verify --config artifacts/portfolio/allocation-config.json --strategy-id momentum_v1 --state approved_live` | Rehydrate config and verify deterministic allocation result. | Validates simulation reproducibility and no DB calls after construction. | read-only | `AllocationConfig.from_dict`, `SimulationAllocationProvider.get_allocation` | P0 |
| `atp portfolio allocation resolve --strategy-id momentum_v1 --state approved_live --performance-tier top --json` | Resolve live allocation for one strategy from policies/overrides/cash snapshot. | Portfolio owns allocation resolution; controls owns changing overrides. | read-only | `PortfolioEngine.get_allocation` via `StrategyAllocationService` helpers | P1 |
| `atp portfolio allocation resolve-many --state approved_live --json` | Resolve allocations for active strategies. | Operators need portfolio allocation preview across strategy universe. | read-only | `StrategyAllocationService.get_allocations_for_active_strategies` | P1 |
| `atp portfolio allocation aggregate --proposed momentum_v1=0.25` | Compute aggregate allocation utilization with optional proposed overrides. | Validates proposed allocation effects without mutating controls. | read-only | `PortfolioEngine.get_aggregate_allocation_pct`, `StrategyAllocationService` projection helpers | P1 |
| `atp portfolio equity-curve --period 1m --json` | Print equity and drawdown curve points. | Core portfolio time-series read model. | read-only | `PortfolioEquityCurveService.get_equity_curve` | P0 |
| `atp portfolio performance --from-date 2026-01-01 --to-date 2026-05-01 --json` | Print total return, CAGR, Sharpe, Sortino, drawdown, volatility, and win rate. | Portfolio performance is a portfolio-domain surface. | read-only | `PortfolioAnalyticsService.get_performance` | P0 |
| `atp portfolio performance-by-period --json` | Print 1M/3M/6M/YTD/1Y returns. | Mirrors REST dashboard data. | read-only | `PortfolioAnalyticsService.get_performance_by_period` | P1 |
| `atp portfolio risk --json` | Print portfolio-facing risk metrics shown by portfolio API. | Portfolio risk view is a dashboard read model; deeper limit logic belongs to risk. | read-only | `PortfolioAnalyticsService.get_risk` | P1 |
| `atp portfolio cash latest --json` | Show latest cash snapshot and freshness metadata. | Cash snapshot is core portfolio state and allocation input. | read-only | `CashSnapshotRepository.get_latest` | P0 |
| `atp portfolio positions latest --json` | Show latest position snapshot and items. | Position snapshot is core portfolio state and risk/allocation input. | read-only | `PositionSnapshotRepository.get_latest` | P0 |
| `atp portfolio reconcile --json` | Check summary, holdings, cash, and position snapshots reconcile. | Essential local verification after simulation/runtime runs. | read-only | `PortfolioSummaryRepository.compute_total_equity`, services above | P1 |
| `atp portfolio construction runs --limit 20 --json` | List recent construction pipeline runs. | Portfolio construction artifacts belong here. | read-only | `PortfolioConstructionRepository.list_runs` | P0 |
| `atp portfolio construction show --batch-id <batch_id>` | Show one construction run's diagnostics. | Core construction debug view. | read-only | `PortfolioConstructionRepository.get_run_by_batch` | P0 |
| `atp portfolio construction by-run-id --run-id <run_id>` | Show latest construction diagnostics for a runtime cycle. | Runtime outputs should be inspectable by portfolio domain. | read-only | `PortfolioConstructionRepository.get_run` | P1 |
| `atp portfolio construction raw-signals --batch-id <batch_id>` | List raw strategy signals for a construction batch. | Verifies phase 1 collection. | read-only | `PortfolioConstructionRepository.list_raw_signals` | P1 |
| `atp portfolio construction netted --batch-id <batch_id>` | List aggregated/netted portfolio signals. | Verifies signal aggregation and conflict resolution. | read-only | `PortfolioConstructionRepository.list_netted_signals` | P0 |
| `atp portfolio construction intents --batch-id <batch_id> --constraint-status rejected` | List constraint-gated signal intents. | Verifies portfolio constraints before orders. | read-only | `PortfolioConstructionRepository.list_signal_intents` | P0 |
| `atp portfolio construction conflicts --batch-id <batch_id>` | List cross-strategy signal conflicts. | Important for portfolio signal netting verification. | read-only | `PortfolioConstructionRepository.list_conflicts` | P0 |
| `atp portfolio construction verify --batch-id <batch_id>` | Assert diagnostics counts match raw/netted/intent artifact rows. | Makes construction artifacts testable and auditable. | read-only | `PortfolioConstructionRepository` aggregate checks | P1 |
| `atp portfolio factor-exposures current --portfolio-id paper --lookback-window 20` | Show portfolio-facing factor exposure snapshot. | Portfolio REST exposes this dashboard view; risk owns deeper monitoring logic. | read-only | `FactorExposureSnapshotRepository.get_latest_snapshot` | P2 |
| `atp portfolio factor-neutralization current --portfolio-id paper` | Show latest factor neutralization run. | Portfolio-facing construction/optimization evidence. | read-only | `FactorNeutralizationRepository.get_latest` | P2 |
| `atp portfolio export --output artifacts/portfolio/current.json` | Emit portfolio summary, holdings, allocation, performance, risk, cash/positions, and construction summary. | Portfolio state should be artifactable for platform workflows. | read-only artifact output | Compose services/repositories above | P1 |
| `atp portfolio verify-dashboard-state --run-id <run_id> --json` | Verify latest cash/position snapshots match portfolio summary/equity curve outputs. | Local dashboard/API-equivalent validation without HTTP. | read-only/platform-adjacent | Existing services plus test patterns from dashboard API real runtime state | P1 |
| `atp portfolio simulate-allocation --config allocation-config.json --strategy-id momentum_v1 --state approved_live --capital 100000` | Simulate allocation result from a frozen config. | Essential for deterministic simulation/backtesting validation. | read-only | `SimulationAllocationProvider.update_total_capital`, `get_allocation` | P0 |

## 4. Testing Plan

Phase 0: help commands

```powershell
atp portfolio --help
atp portfolio snapshot --help
atp portfolio summary --help
atp portfolio holdings --help
atp portfolio allocation --help
atp portfolio allocation resolve --help
atp portfolio allocation-config snapshot --help
atp portfolio allocation-config verify --help
atp portfolio equity-curve --help
atp portfolio performance --help
atp portfolio performance-by-period --help
atp portfolio risk --help
atp portfolio cash latest --help
atp portfolio positions latest --help
atp portfolio reconcile --help
atp portfolio construction runs --help
atp portfolio construction show --help
atp portfolio construction netted --help
atp portfolio construction intents --help
atp portfolio construction conflicts --help
atp portfolio export --help
atp portfolio simulate-allocation --help
```

Phase 1: safe read-only commands

```powershell
atp portfolio snapshot --json
atp portfolio summary --json
atp portfolio holdings --json
atp portfolio allocation --json
atp portfolio allocation resolve --strategy-id momentum_v1 --state approved_live --performance-tier top --json
atp portfolio allocation aggregate --proposed momentum_v1=0.25
atp portfolio allocation-config show --json
atp portfolio allocation-config snapshot --output artifacts/portfolio/allocation-config.json
atp portfolio allocation-config verify --config artifacts/portfolio/allocation-config.json --strategy-id momentum_v1 --state approved_live
atp portfolio equity-curve --period 1m --json
atp portfolio performance --from-date 2026-01-01 --to-date 2026-05-01 --json
atp portfolio performance-by-period --json
atp portfolio risk --json
atp portfolio cash latest --json
atp portfolio positions latest --json
atp portfolio reconcile --json
atp portfolio construction runs --limit 20 --json
atp portfolio construction show --batch-id 11111111-1111-4111-8111-111111111111
atp portfolio construction netted --batch-id 11111111-1111-4111-8111-111111111111
atp portfolio construction intents --batch-id 11111111-1111-4111-8111-111111111111 --constraint-status rejected
atp portfolio construction conflicts --batch-id 11111111-1111-4111-8111-111111111111
atp portfolio simulate-allocation --config artifacts/portfolio/allocation-config.json --strategy-id momentum_v1 --state approved_live --capital 100000
atp portfolio export --output artifacts/portfolio/current.json
```

Phase 2: local DB mutation commands

No P0/P1 portfolio CLI commands should mutate local DB state. Portfolio mutation surfaces should stay in `controls` for manual allocation overrides, `execution` for fills/orders/cash/positions, and `runtime` for scheduled cycles. If a future portfolio command persists construction artifacts from a synthetic input fixture, it should require `--persist`, `--updated-by`, `--reason`, and `--dry-run` support.

Phase 3: cross-domain/runtime commands

```powershell
atp portfolio verify-dashboard-state --run-id 11111111-1111-4111-8111-111111111111 --json
atp portfolio construction by-run-id --run-id 11111111-1111-4111-8111-111111111111
atp portfolio construction verify --batch-id 11111111-1111-4111-8111-111111111111
```

Phase 4: broker/external commands

Portfolio CLI should default to local DB snapshots. Broker-backed portfolio reads may be useful later, but they should be explicit and guarded:

```powershell
atp portfolio summary --broker --account-id paper --json
atp portfolio holdings --broker --account-id paper --json
```

These broker modes should never place orders and should respect simulation mode fallback.

## 5. Risks / Suspicious Wiring

- `src/autonomous_trading_platform/cli/commands/portfolio.py` does not exist.
- `src/autonomous_trading_platform/cli/main.py` does not import or register a `portfolio` domain.
- Portfolio state inspection currently lives under `backtesting read-portfolio`, which is misleading because it reads current DB/API-equivalent portfolio state.
- REST portfolio `summary`, `holdings`, and `allocation` may call Alpaca when an Alpaca client is available and runtime mode is not simulation. A CLI should default to local DB reads and require explicit `--broker` for external calls.
- REST allocation falls back to configured strategy allocation percentages when live holdings are empty. The CLI should make that fallback visible in metadata so users know whether they are seeing actual holdings or configured targets.
- `PortfolioAnalyticsService.get_risk()` returns portfolio dashboard metrics with beta and average pairwise correlation currently set to zero. Deeper limit and exposure logic belongs in `risk`; portfolio CLI should label this as dashboard risk, not full risk enforcement.
- `PortfolioConstructionRepository._persist_batch_items()` is a placeholder `pass`; raw signal persistence requires explicit `persist_raw_signals()`. Construction artifact verification should flag batches with missing raw signal rows.
- The Portfolio Construction Layer (Rec 6.5) introduced a two-phase pipeline: Collect→Aggregate→Constrain→Generate. It added 4 SOR tables (`portfolio_construction_runs`, `portfolio_raw_signals`, `portfolio_netted_signals`, `portfolio_signal_intents`) and 9 netting policies via `PortfolioSignalAggregator`. Construction runs are stored under `portfolio_construction_runs` and are queryable by `batch_id` or `run_id`. CLI commands for construction artifacts should support both lookup keys.
- `PortfolioSignalAggregator` (FINDING-08) implements cross-strategy signal conflict detection with 4 base policies and is extended by the construction layer. CLI conflict inspection (`construction conflicts`) should surface the netting policy used and per-symbol conflict reasons.
- `SimulationAllocationProvider` is the deterministic allocation path for backtesting and simulation. It is initialized from a frozen `AllocationConfig` snapshot and must not touch the live DB during allocation resolution. `portfolio simulate-allocation` and `portfolio allocation-config verify` should validate this no-DB guarantee.
- Portfolio construction route signatures use defaults like `session: ... = None`; this works through FastAPI dependency injection but would be suspicious if copied directly into CLI handlers.
- Allocation config snapshot/replay is important recent logic but has no CLI surface. Without one, deterministic simulation allocation behavior is hard to verify outside tests.
- Portfolio allocation mutation should not be implemented here. Manual allocation override writes belong to `controls`; portfolio should resolve, preview, verify, and export.
- Commands that emit portfolio snapshots, allocation configs, and construction diagnostics should support JSON/artifact output.
- If future portfolio commands persist synthetic construction artifacts, they need `--dry-run`, `--persist`, actor/reason, and audit logging.

## 6. Recommended Refactor / Extension

- Add `src/autonomous_trading_platform/cli/commands/portfolio.py` and register it in `src/autonomous_trading_platform/cli/main.py`.
- Add P0 read commands first: `snapshot`, `summary`, `holdings`, `allocation`, `equity-curve`, `performance`, `cash latest`, `positions latest`, construction `runs/show/netted/intents/conflicts`, and allocation-config `show/snapshot/verify`.
- Move or wrap `backtesting read-portfolio` as `portfolio snapshot`.
- Keep all portfolio CLI commands local-read-only by default.
- Add explicit `--broker` only for broker-backed summary/holdings/allocation reads, with clear safety boundaries.
- Add JSON and artifact output for portfolio snapshots, allocation configs, construction diagnostics, and reconciliation reports.
- Add construction artifact verification to catch missing raw signal rows and count mismatches.
- Keep allocation override mutation under `controls`, risk enforcement under `risk`, and scheduled allocation/rebalance cycles under `runtime` or `governance`.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp portfolio` | Missing | yes | Medium | Create `portfolio.py` and register the domain. |
| `atp portfolio snapshot` | Missing; legacy exists as `backtesting read-portfolio` | yes | Low | Move/wrap legacy read command. |
| `atp portfolio summary` | Missing | yes | Low | Implement local DB read from `PortfolioSummaryService`. |
| `atp portfolio holdings` | Missing | yes | Low locally, Medium with broker | Implement local default; optional explicit broker mode later. |
| `atp portfolio allocation` | Missing | yes | Low | Implement API-equivalent allocation with fallback metadata. |
| `atp portfolio allocation resolve` | Missing | yes | Low | Add single-strategy allocation resolution. |
| `atp portfolio allocation aggregate` | Missing | yes | Low | Add aggregate allocation preview, no mutation. |
| `atp portfolio allocation-config show/snapshot` | Missing | yes | Low | Expose deterministic allocation config and hash. |
| `atp portfolio allocation-config verify` | Missing | yes | Low | Rehydrate config and verify allocation behavior. |
| `atp portfolio simulate-allocation` | Missing | yes | Low | Use `SimulationAllocationProvider` for deterministic checks. |
| `atp portfolio equity-curve` | Missing | yes | Low | Implement from `PortfolioEquityCurveService`. |
| `atp portfolio performance` | Missing | yes | Low | Implement from `PortfolioAnalyticsService`. |
| `atp portfolio performance-by-period` | Missing | yes | Low | Implement dashboard period returns. |
| `atp portfolio risk` | Missing | partial | Low | Implement as dashboard risk; defer enforcement to `risk`. |
| `atp portfolio cash latest` | Missing | yes | Low | Add cash snapshot inspection. |
| `atp portfolio positions latest` | Missing | yes | Low | Add position snapshot inspection. |
| `atp portfolio reconcile` | Missing | yes | Medium | Add consistency checks across cash/positions/summary/holdings. |
| `atp portfolio construction runs/show` | Missing | yes | Low | Wrap construction repository reads. |
| `atp portfolio construction raw-signals` | Missing | yes | Medium | Add and flag missing raw rows due repository placeholder path. |
| `atp portfolio construction netted/intents/conflicts` | Missing | yes | Low | Wrap construction artifact reads. |
| `atp portfolio construction verify` | Missing | yes | Medium | Add count/reconciliation checks for construction artifacts. |
| `atp portfolio factor-exposures current` | Missing | partial | Low | Optional portfolio-facing read wrapper; risk owns computation. |
| `atp portfolio factor-neutralization current` | Missing | partial | Low | Optional portfolio-facing optimization evidence read. |
| `atp portfolio export` | Missing | yes | Low | Emit portfolio state artifact. |
| `atp portfolio verify-dashboard-state` | Missing | partial | Medium | Add local platform-adjacent verification from runtime outputs. |
| `atp backtesting read-portfolio` | Existing legacy | no | Medium | Deprecate after `portfolio snapshot` exists. |
