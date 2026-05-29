# Risk CLI Domain Audit: `risk.py`

Target CLI domain: `risk`

Target CLI file: `src/autonomous_trading_platform/cli/commands/risk.py`

Audit status: target file does not exist yet. This audit records the empty current inventory and the proposed risk-domain entrypoints needed for capital/trading constraints, drawdown, exposure, concentration, volatility, limits, risk budgeting, and verification.

Domain definition: `risk` owns capital and trading constraints: drawdown limits, exposure and concentration limits, leverage, volatility targeting, factor exposure, risk budgets, risk snapshots, and proof that those controls affect runtime behavior. It should not own kill switch/live gate/emergency halt (`safety`), pause/resume/trading mode/strategy toggles/manual allocation overrides (`controls`), persisted operator configuration (`settings`), scheduler orchestration (`runtime`), portfolio reporting (`portfolio`), or full product smoke workflows (`platform`).

## 1. Current CLI Inventory

No commands are registered in `src/autonomous_trading_platform/cli/commands/risk.py` because the file does not exist. The `risk` domain is also not registered in `src/autonomous_trading_platform/cli/main.py`.

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp risk` | none | none | no | no | no | PLACEHOLDER |

Risk-like commands currently live elsewhere:

| Existing command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp backtesting verify-risk-parameter-effects` | `--controls`, `--settings`, `--symbols`, `--start`, `--end`, `--starting-cash`, `--random-seed`, `--reset-sim-state`, `--print-summary`, repeatable `--parameter {max_portfolio_drawdown,max_strategy_drawdown,risk_tolerance,max_capital_per_strategy,target_portfolio_volatility}` | `handle_verify_risk_parameter_effects` | yes/conditional | no | no | CROSS_DOMAIN_RUNTIME |
| Runtime/scheduler risk jobs | not directly registered as CLI commands | `run_risk_budgeting_cycle`, `run_drawdown_governance_ladder_cycle`, `run_factor_exposure_monitoring_cycle`, `run_risk_snapshot_job` | yes | no | no | CROSS_DOMAIN_RUNTIME |

## 2. Domain Responsibility Check

| Command | Classification | Correct domain | Notes |
|---|---|---|---|
| `atp risk` | correctly placed, missing | risk | The domain belongs in the final CLI taxonomy but has not been implemented. |
| `atp backtesting verify-risk-parameter-effects` | should move to another domain | risk, with runtime/platform wrappers | It verifies risk parameters materially change replay/runtime behavior. Primary ownership is risk; runtime/platform can wrap it for broader workflows. |
| `run_risk_budgeting_cycle` | should be duplicated/wrapped elsewhere | risk and runtime | The computation is risk-domain; the scheduler cycle wrapper is runtime orchestration. |
| `run_drawdown_governance_ladder_cycle` | should be duplicated/wrapped elsewhere | risk and runtime/governance | Drawdown limits are risk; lifecycle governance evidence may also be surfaced in governance. |
| `run_factor_exposure_monitoring_cycle` | should be duplicated/wrapped elsewhere | risk and runtime | Factor exposure is risk-domain; scheduled execution is runtime. |
| `run_risk_snapshot_job` | should be duplicated/wrapped elsewhere | risk and runtime/execution | Risk snapshot metrics belong to risk; trading-cycle job orchestration belongs to runtime/execution. |
| Pre-trade risk checks in `safety.services.pre_trade_risk_service` | should be duplicated/wrapped elsewhere | risk and safety | Constraint evaluation is risk-domain; hard blocking enforcement in the order path is safety/execution-critical. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp risk limits show` | Show active risk limits: gross/net exposure, leverage, symbol concentration, sector limits, drawdown limits, volatility target. | Operators need to inspect constraints before running or changing workflows. | read-only | `Settings`, `OperatorSettingsService.get_settings`, `RiskLimitConfig` builder | P0 |
| `atp risk snapshot latest --json` | Print latest persisted risk snapshot. | Risk snapshots are the primary evidence for exposure/leverage/drawdown blocking. | read-only | `RiskSnapshotRepository.get_latest` | P0 |
| `atp risk snapshot history --limit 20` | List recent risk snapshots and blocked reasons. | Needed to debug changing risk state over time. | read-only | `RiskSnapshotRepository` query helpers | P1 |
| `atp risk snapshot compute --run-id <uuid> --as-of 2026-05-04T00:00:00Z --dry-run` | Compute risk snapshot from latest cash/position snapshots without persisting. | Validates limits and utilization safely. | read-only | `RiskSnapshotService.compute_snapshot` | P0 |
| `atp risk snapshot compute --run-id <uuid> --as-of 2026-05-04T00:00:00Z --persist` | Compute and persist a risk snapshot. | Manual/local replay of the risk snapshot job. | local-mutating | `RiskSnapshotService.compute_snapshot`, `RiskSnapshotRepository.upsert` | P1 |
| `atp risk exposure current --json` | Show gross/net/long/short exposure plus per-strategy and per-sector breakdown. | Exposure and concentration are core risk-domain responsibilities. | read-only | `RiskEngine.compute_exposures` using latest position snapshot and optional maps | P0 |
| `atp risk concentration current --top 10` | Show top symbol/sector/strategy concentration and limit utilization. | Concentration limits need direct inspection. | read-only | `PortfolioRiskStateReader`, `SectorExposureReader`, `RiskSnapshotService` utilization helpers | P0 |
| `atp risk pretrade check --symbol AAPL --side buy --qty 10 --limit-price 190 --strategy-id momentum_v1 --json` | Evaluate whether a proposed order would pass risk constraints without sending it. | Pre-trade limit evaluation is risk-domain; no broker call should occur. | read-only | `PreTradeRiskService.assert_order_allowed` with DB-backed readers | P1 |
| `atp risk drawdown list --state breached --json` | List strategy drawdown ladder states. | Drawdown constraints and allocation scalars belong to risk. | read-only | `DrawdownGovernanceLadderStateRepository.get_by_state` | P0 |
| `atp risk drawdown show --strategy-id momentum_v1` | Show one strategy's drawdown utilization, ladder rung, scalar, cooldown, and ack status. | Direct drawdown-risk inspection. | read-only | `DrawdownGovernanceLadderStateRepository.get_for_strategy` | P0 |
| `atp risk drawdown transitions --strategy-id momentum_v1 --limit 50` | Show ladder transition history. | Needed audit trail for drawdown-risk decisions. | read-only | `DrawdownGovernanceLadderTransitionRepository.get_recent_for_strategy` | P1 |
| `atp risk drawdown pending-ack` | List breached strategies requiring operator acknowledgement. | Drawdown recovery workflow needs an operable queue. | read-only | `DrawdownGovernanceLadderStateRepository.get_pending_operator_ack` | P0 |
| `atp risk drawdown acknowledge --strategy-id momentum_v1 --operator risk-manager --reason "reviewed breach"` | Acknowledge a breach so recovery can proceed. | This is a risk-specific operator action, not a generic control. | local-mutating | `DrawdownGovernanceService.acknowledge_breach` | P0 |
| `atp risk drawdown evaluate --dry-run` | Evaluate drawdown ladder without persisting state. | Operators need safe preview before enforce-mode changes. | read-only | Add dry-run/observe-only wrapper around `DrawdownGovernanceService.run` or config mode `observe` | P0 |
| `atp risk drawdown evaluate --persist --run-id <id>` | Run drawdown ladder evaluation and persist transitions/audit events. | Manual execution of risk ladder evaluation. | local-mutating | `DrawdownGovernanceService.run` | P1 |
| `atp risk budget compute --strategy-id momentum_v1 --strategy-id mean_reversion_v1 --mode equal_risk_contribution --dry-run` | Compute risk-budgeted weights without persisting. | Risk budgeting is advisory risk-domain computation. | read-only | `RiskBudgetingService.compute` needs a non-persist option or preview method | P0 |
| `atp risk budget run --strategy-id momentum_v1 --strategy-id mean_reversion_v1 --mode equal_risk_contribution` | Compute and persist risk budget snapshot. | Manual/local run of risk budgeting. | local-mutating | `RiskBudgetingService.compute` or `run_risk_budgeting_cycle` | P1 |
| `atp risk budget latest --json` | Show latest persisted risk budget snapshot. | Needed to inspect recommendations and hidden concentration warnings. | read-only | `RiskBudgetingService.get_latest_snapshot` | P1 |
| `atp risk factor latest --portfolio-id default --lookback-window 60` | Show latest factor exposure snapshot. | Factor exposure and concentration are risk-domain diagnostics. | read-only | `FactorExposureMonitoringService.get_latest_snapshot` | P1 |
| `atp risk factor history --since 2026-05-01T00:00:00Z --limit 50` | List recent factor exposure snapshots and alerts. | Risk operators need factor drift/concentration history. | read-only | `FactorExposureMonitoringService.get_history` | P2 |
| `atp risk factor run --as-of 2026-05-04T00:00:00Z --portfolio-id default --dry-run` | Compute factor exposures without persisting. | Safe preview for factor-risk computation. | read-only | `FactorExposureMonitoringService.run` needs non-persist option or preview wrapper | P2 |
| `atp risk correlation current --portfolio-id default --json` | Show latest correlation/covariance snapshot and cluster warnings. | `CorrelationMonitoringService` produces observability-only correlation snapshots with greedy cluster detection; no CLI surface exists. | read-only | `CorrelationSnapshotRow` repository query, `CorrelationMonitoringService.get_latest_snapshot` | P2 |
| `atp risk mv-optimize --strategy-id momentum_v1 --strategy-id mean_reversion_v1 --objective min_variance --dry-run` | Compute mean-variance optimal weights without applying them. | `MeanVarianceOptimizer` is advisory only (dry_run=True default); no CLI surface exists to inspect optimizer runs or results. | read-only | `MeanVarianceOptimizer.optimize`, `OptimizerRunRow` repository | P2 |
| `atp risk verify-parameter-effects --controls fixtures/controls.yaml --settings fixtures/settings.yaml --symbols SPY,QQQ --start 2026-01-01 --end 2026-03-31 --starting-cash 100000 --random-seed 42 --reset-sim-state --print-summary` | Prove risk settings change replay outputs and risk snapshots. | Direct replacement for legacy backtesting verification. | cross-domain/runtime | Split/wrap `handle_verify_risk_parameter_effects`, `RuntimeReplayDebugRunner` | P0 |
| `atp risk audit-log --limit 20` | Show recent risk-related audit events. | Risk mutations and breaches need traceability. | read-only | `AuditLogRepository`, governance audit repositories, event filters | P1 |
| `atp risk export --output artifacts/risk/current.json` | Emit current limits, latest risk snapshot, drawdown states, risk budget, and factor exposure summary. | Risk state should be artifactable for audits and workflow bundles. | read-only artifact output | Compose services/repositories above | P1 |

## 4. Testing Plan

Phase 0: help commands

```powershell
atp risk --help
atp risk limits --help
atp risk limits show --help
atp risk snapshot --help
atp risk snapshot latest --help
atp risk snapshot history --help
atp risk snapshot compute --help
atp risk exposure current --help
atp risk concentration current --help
atp risk pretrade check --help
atp risk drawdown --help
atp risk drawdown list --help
atp risk drawdown show --help
atp risk drawdown transitions --help
atp risk drawdown pending-ack --help
atp risk drawdown acknowledge --help
atp risk drawdown evaluate --help
atp risk budget --help
atp risk budget compute --help
atp risk budget run --help
atp risk budget latest --help
atp risk factor latest --help
atp risk factor history --help
atp risk verify-parameter-effects --help
atp risk audit-log --help
atp risk export --help
```

Phase 1: safe read-only commands

```powershell
atp risk limits show --json
atp risk snapshot latest --json
atp risk snapshot history --limit 20
atp risk snapshot compute --run-id 11111111-1111-4111-8111-111111111111 --as-of 2026-05-04T00:00:00Z --dry-run
atp risk exposure current --json
atp risk concentration current --top 10
atp risk pretrade check --symbol AAPL --side buy --qty 10 --limit-price 190 --strategy-id momentum_v1 --json
atp risk drawdown list --json
atp risk drawdown pending-ack
atp risk drawdown show --strategy-id momentum_v1
atp risk drawdown transitions --strategy-id momentum_v1 --limit 50
atp risk drawdown evaluate --dry-run
atp risk budget compute --strategy-id momentum_v1 --strategy-id mean_reversion_v1 --mode equal_risk_contribution --dry-run
atp risk budget latest --json
atp risk factor latest --portfolio-id default --lookback-window 60
atp risk audit-log --limit 20
atp risk export --output artifacts/risk/current.json
```

Phase 2: local DB mutation commands

```powershell
atp risk snapshot compute --run-id 11111111-1111-4111-8111-111111111111 --as-of 2026-05-04T00:00:00Z --persist
atp risk drawdown evaluate --persist --run-id local-drawdown-risk-check
atp risk drawdown acknowledge --strategy-id momentum_v1 --operator risk-manager --reason "reviewed drawdown breach"
atp risk budget run --strategy-id momentum_v1 --strategy-id mean_reversion_v1 --mode equal_risk_contribution
atp risk audit-log --limit 20
```

Phase 3: cross-domain/runtime commands

```powershell
atp risk verify-parameter-effects --controls fixtures/controls.yaml --settings fixtures/settings.yaml --symbols SPY,QQQ --start 2026-01-01 --end 2026-03-31 --starting-cash 100000 --random-seed 42 --reset-sim-state --print-summary
atp risk verify-parameter-effects --controls fixtures/controls.yaml --settings fixtures/settings.yaml --symbols AAPL,MSFT --start 2026-05-04 --end 2026-05-08 --parameter max_strategy_drawdown --parameter target_portfolio_volatility --starting-cash 100000 --random-seed 42 --output artifacts/risk/parameter-effects.json
```

Phase 4: broker/external commands

No risk-domain command should place orders or call broker APIs directly. `risk pretrade check` should evaluate a hypothetical order locally. Live gate, kill switch, emergency halt, and broker order cancellation belong in `safety` or `execution`.

## 5. Risks / Suspicious Wiring

- `src/autonomous_trading_platform/cli/commands/risk.py` does not exist.
- `src/autonomous_trading_platform/cli/main.py` does not import or register a `risk` domain.
- Legacy `backtesting verify-risk-parameter-effects` is the right behavior but wrong domain. It mutates/seeds local settings and controls, runs replay runtime, and should be exposed as explicit cross-domain risk verification.
- `verify-risk-parameter-effects` should emit machine-readable artifacts by default; summary-only output is insufficient for audit evidence.
- Risk snapshot computation is currently primarily scheduler-job oriented. A CLI wrapper needs clear `--dry-run` vs `--persist` behavior.
- `RiskBudgetingService.compute()` persists as part of compute. A CLI `risk budget compute --dry-run` needs a non-persist preview path or a separate pure compute method.
- `FactorExposureMonitoringService.run()` persists snapshots as part of run. A CLI dry-run needs a non-persist preview path.
- `DrawdownGovernanceService.run()` can persist and audit depending on mode. The CLI should make observe/dry-run versus persist/enforce explicit.
- `DrawdownGovernanceService._load_config()` reads non-existent or optional operator settings fields via `getattr` defaults; CLI should surface the effective config and source clearly.
- `DrawdownGovernanceService._do_evaluate_strategy()` calls `OperatorSettingsRepository.get_or_create_default()`, so evaluation may create a default settings row.
- Pre-trade risk enforcement lives in `safety.services.pre_trade_risk_service`, but the logic is risk-domain. A CLI wrapper must not send orders or bypass execution safety.
- Risk limits are split across environment settings, operator settings, allocation policies, allocation overrides, and drawdown ladder config. `risk limits show` should identify source-of-truth per limit.
- Risk-domain mutating commands need audit logging and artifact/JSON output.
- Risk-domain commands should not duplicate `controls allocation set`; allocation overrides are operator controls, while risk may validate/preview utilization and budget effects.
- Commands that can indirectly influence live behavior, especially drawdown acknowledge and live-mode parameter verification, should require explicit actor/reason and avoid broker calls.
- The drawdown governance ladder (`DrawdownGovernanceLadderService`) implements a 5-rung progressive ladder: NORMAL→WARNING→PROBATION→SUSPENDED→BREACHED, with hysteresis and cooldown anti-flapping between rung transitions. The ladder state is stored in `drawdown_governance_ladder_states` and `drawdown_governance_ladder_transitions` SOR tables. CLI commands that read or evaluate the ladder should surface rung state, cooldown, hysteresis window, and acknowledgement status.
- `RiskBudgetingService` supports 4 allocation modes: `equal_capital`, `equal_vol`, `equal_risk_contribution` (ERC via CCD solver), and `fixed`. The `compute()` method persists a `RiskBudgetSnapshotRow` by default; a CLI dry-run needs a non-persist wrapper or a separate pure-compute method.
- `SectorExposureReader` performs sector lookups and is integrated into `PreTradeRiskService` for sector concentration limit checks. `risk pretrade check` should call `PreTradeRiskService.assert_order_allowed` which already uses this reader.
- `CorrelationMonitoringService` is currently observability-only (Phase 5A); no enforcement or blocking behavior exists. `risk correlation current` should label snapshots accordingly.
- `MeanVarianceOptimizer` defaults to `dry_run=True`; all optimizer runs are advisory and stored in `OptimizerRunRow`. It is not yet integrated into the allocation pipeline.

## 6. Recommended Refactor / Extension

- Add `src/autonomous_trading_platform/cli/commands/risk.py` and register it in `src/autonomous_trading_platform/cli/main.py`.
- Add P0 read commands first: `limits show`, `snapshot latest`, `snapshot compute --dry-run`, `exposure current`, `concentration current`, `drawdown list/show/pending-ack`, `drawdown evaluate --dry-run`, and `budget compute --dry-run`.
- Move or wrap `backtesting verify-risk-parameter-effects` as `risk verify-parameter-effects`.
- Add explicit `--dry-run`/`--persist` switches for snapshot, drawdown, risk budget, and factor workflows.
- Add JSON and artifact output for all risk reports and verification commands.
- Add audited service-backed mutation for `drawdown acknowledge`.
- Keep kill switch/live gate under `safety` and manual allocation override writes under `controls`.
- Add shared source-of-truth metadata for limits so settings, controls, risk, and portfolio surfaces do not drift.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp risk` | Missing | yes | Medium | Create `risk.py` and register the domain. |
| `atp risk limits show` | Missing | yes | Low | Implement effective limit/source inspection. |
| `atp risk snapshot latest` | Missing | yes | Low | Read latest persisted `RiskSnapshot`. |
| `atp risk snapshot history` | Missing | yes | Low | Add bounded history query. |
| `atp risk snapshot compute --dry-run` | Missing | yes | Low | Compute from latest cash/positions without persisting. |
| `atp risk snapshot compute --persist` | Missing | yes | Medium | Persist snapshot with explicit flag. |
| `atp risk exposure current` | Missing | yes | Low | Implement from `RiskEngine` and latest position snapshot. |
| `atp risk concentration current` | Missing | yes | Low | Implement from portfolio/sector risk readers. |
| `atp risk pretrade check` | Missing | yes | Medium | Add local-only hypothetical order check; no broker calls. |
| `atp risk drawdown list/show` | Missing | yes | Low | Implement from drawdown ladder repositories. |
| `atp risk drawdown pending-ack` | Missing | yes | Low | Add pending acknowledgement queue. |
| `atp risk drawdown acknowledge` | Missing | yes | Medium | Implement via `DrawdownGovernanceService.acknowledge_breach` with actor/reason. |
| `atp risk drawdown evaluate --dry-run` | Missing | yes | Medium | Add non-persist preview path. |
| `atp risk drawdown evaluate --persist` | Missing | yes | Medium | Implement explicit persisted evaluation. |
| `atp risk budget compute --dry-run` | Missing | yes | Medium | Add non-persist risk budget preview. |
| `atp risk budget run` | Missing | yes | Medium | Implement persisted risk budget computation. |
| `atp risk budget latest` | Missing | yes | Low | Read latest risk budget snapshot. |
| `atp risk factor latest/history` | Missing | yes | Low | Read factor exposure snapshots. |
| `atp risk factor run --dry-run` | Missing | yes | Medium | Add non-persist factor preview before exposing persisted run. |
| `atp risk verify-parameter-effects` | Missing; legacy exists as `backtesting verify-risk-parameter-effects` | yes | Medium | Move/wrap legacy handler and emit artifacts. |
| `atp risk audit-log` | Missing | yes | Low | Show risk-related audit events. |
| `atp risk export` | Missing | yes | Low | Emit limits/snapshot/drawdown/budget/factor artifact. |
| `atp backtesting verify-risk-parameter-effects` | Existing legacy | no | Medium | Migrate to `risk`, keep temporary compatibility wrapper if needed. |
