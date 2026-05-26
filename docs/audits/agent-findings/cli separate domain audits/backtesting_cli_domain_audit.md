# Backtesting CLI Domain Audit

Target CLI domain: `backtesting`
Target CLI file: `src/autonomous_trading_platform/cli/commands/backtesting.py`
Migration note: this audit assumes `backtesting` is deprecated and should be emptied/removed after replacement commands exist in final CLI domains.

## 1. Current CLI Inventory

| Command Path | Arguments / Options | Handler | Mutates State? | Calls External APIs? | Safe For Local Read-Only Testing? | Phase Classification |
|---|---|---|---:|---:|---:|---|
| `backtesting run` | `--timestamp` | `handle_run(args)` | no | no | yes | `PLACEHOLDER` |
| `backtesting inspect-results` | `--run-id` required | `handle_inspect_results(args)` | no | no | yes | `PLACEHOLDER` |
| `backtesting seed-fixture` | `--fixture` required; `--dry-run` | `handle_seed_fixture(args)` | conditional | no | yes only with `--dry-run` | `LOCAL_DB_MUTATION` |
| `backtesting seed-settings` | `--config` required | `handle_seed_settings(args)` | yes | no | no | `LOCAL_DB_MUTATION` |
| `backtesting read-settings` | none | `handle_read_settings(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `backtesting seed-controls` | `--config` required; `--clean` | `handle_seed_controls(args)` | yes | no | no | `LOCAL_DB_MUTATION` |
| `backtesting read-controls` | none | `handle_read_controls(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `backtesting read-portfolio` | none | `handle_read_portfolio(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `backtesting read-dashboard` | none | `handle_read_dashboard(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `backtesting verify-risk-parameter-effects` | `--controls` required; `--settings` required; `--symbols` required; `--start` required; `--end` required; `--starting-cash`; `--random-seed`; `--reset-sim-state`; `--print-summary`; repeatable `--parameter {max_portfolio_drawdown,max_strategy_drawdown,risk_tolerance,max_capital_per_strategy,target_portfolio_volatility}` | `handle_verify_risk_parameter_effects(args)` | yes | no | no | `PLATFORM_WORKFLOW` |
| `backtesting verify-notification-events` | `--controls` required; `--settings` required | `handle_verify_notification_events(args)` | yes | no | no | `PLATFORM_WORKFLOW` |
| `backtesting verify-governance-allocation` | `--controls` required; `--settings` required; `--total-capital` | `handle_verify_governance_allocation(args)` | yes | no | no | `PLATFORM_WORKFLOW` |
| `backtesting verify-auto-promotion` | `--settings` required | `handle_verify_auto_promotion(args)` | conditional/transient | no | no | `LOCAL_DB_MUTATION` |
| `backtesting verify-auto-demotion` | `--settings` required | `handle_verify_auto_demotion(args)` | conditional/transient | no | no | `LOCAL_DB_MUTATION` |

Notes:
- `run` and `inspect-results` only print `not_implemented`.
- Seed commands are fixture/admin helpers, not backtest execution.
- Read commands mirror frontend/API state for settings, controls, portfolio, and dashboard.
- Verification commands are useful, but they exercise risk, operations notifications, governance, portfolio allocation, and platform flows. They should not remain under a deprecated `backtesting` domain.
- No command appears broker-facing; the risk is local DB mutation and cross-domain state manipulation, not live broker calls.

## 2. Domain Responsibility Check

| Current Command | Placement | Proposed New Home | Assessment |
|---|---|---|---|
| `backtesting run` | should be deprecated | `platform backtest run` if rebuilt; otherwise remove | Current handler is a placeholder. Do not migrate as-is unless implemented against the canonical platform backtest runner. |
| `backtesting inspect-results` | should be deprecated | `platform backtest inspect` or `platform backtest report` | Current handler is a placeholder. Replace with artifact/report inspection, not a direct move. |
| `backtesting seed-fixture` | should move | `platform fixture seed` | Seeds multi-domain scenario fixtures: strategies, governance, controls, allocations, settings. This is a platform scenario setup tool. |
| `backtesting seed-settings` | should move | `settings seed` or `settings apply-fixture` | Operator settings belong in `settings`; keep fixture wording explicit. |
| `backtesting read-settings` | should move | `settings show` | Read-only persisted settings inspection belongs in `settings`. |
| `backtesting seed-controls` | should move | `controls seed` or `controls apply-fixture` | Runtime controls, strategy toggles, and allocation overrides belong primarily in `controls`; allocation-specific pieces may later split to `portfolio`. |
| `backtesting read-controls` | should move | `controls show` | Read-only current control state belongs in `controls`. |
| `backtesting read-portfolio` | should move | `portfolio snapshot` | Portfolio API-equivalent state belongs in `portfolio`. |
| `backtesting read-dashboard` | should move | `platform dashboard-snapshot` | Bundled frontend/dashboard state is a product workflow validation surface, so `platform` is the best home. |
| `backtesting verify-risk-parameter-effects` | should move | `risk verify-parameter-effects` | Verifies risk/capital constraints actually affect replay behavior. Primary owner is `risk`; platform can wrap it in a bundle. |
| `backtesting verify-notification-events` | should move | `operations verify-notification-events` | This is operational alert/notification verification. |
| `backtesting verify-governance-allocation` | should move | `governance verify-allocation` or `portfolio verify-governance-allocation` | It verifies governance state, promotion rules, allocation policies, and portfolio allocation. Preferred owner is `governance`, with portfolio details in output. |
| `backtesting verify-auto-promotion` | should move | `governance verify-auto-promotion` | Directly verifies auto-promotion service behavior and audit/notification effects. |
| `backtesting verify-auto-demotion` | should move | `governance verify-auto-demotion` | Directly verifies auto-demotion service behavior and audit/notification effects. |

## 3. Missing CLI Coverage

These are the replacement entrypoints needed to remove `backtesting` without losing useful work:

| Proposed Command Path | Purpose | Why It Belongs In This Domain | Type | Implementation Target | Priority |
|---|---|---|---|---|---|
| `platform backtest plan` | Validate and print the intended historical product workflow without mutation. | Replaces the conceptual planning part of `backtesting run` with a real platform-level preflight. | read-only | Existing runtime replay/backtest orchestrator planning plus dataset/universe/feature checks | P0 |
| `platform backtest run` | Run canonical end-to-end historical workflow and emit artifact bundle. | If backtesting remains conceptually needed, it is a platform workflow, not a separate domain. | platform-level | `BacktestTradingCycleOrchestrator`, `runtime replay`, or the chosen canonical research simulation path | P0 |
| `platform backtest inspect --run-id ...` | Inspect a saved platform backtest run/artifact. | Replaces placeholder `backtesting inspect-results`. | read-only | Run manifests, simulation runs, artifact bundle reader | P0 |
| `platform backtest report --artifact artifacts/platform/backtest/.../bundle.json` | Summarize completed backtest artifacts for humans/CI. | Product workflow outputs should be reportable from `platform`. | read-only artifact output | Platform artifact bundle reader | P1 |
| `platform fixture seed --fixture fixtures/platform_scenario.yaml --dry-run` | Seed multi-domain scenario fixtures. | Direct replacement for `backtesting seed-fixture`. | local-mutating, dry-run supported | Existing `handle_seed_fixture` logic split into a platform fixture service | P0 |
| `settings seed --config fixtures/settings.yaml --dry-run` | Validate/apply settings fixture. | Direct replacement for `backtesting seed-settings`, with dry-run added. | local-mutating | `OperatorSettingsRepository.update_current`, preferably `OperatorSettingsService` | P0 |
| `settings show --format json` | Print current operator settings. | Direct replacement for `backtesting read-settings`. | read-only | `OperatorSettingsRepository.get_current` or `OperatorSettingsService` | P0 |
| `controls seed --config fixtures/controls.yaml --clean --dry-run` | Seed runtime controls, strategy toggles, and allocation fixture state. | Direct replacement for `backtesting seed-controls`; belongs with pause/mode/toggles. | local-mutating | `RuntimeControlStateRepository`, `StrategyControlStateRepository`, allocation override repo | P0 |
| `controls show --format json` | Print global runtime controls, strategy toggles, allocation overrides, pending promotion. | Direct replacement for `backtesting read-controls`. | read-only | Existing `handle_read_controls` query logic or `RuntimeControlService.snapshot` | P0 |
| `portfolio snapshot --format json` | Print API-equivalent portfolio summary, holdings, allocation, risk, performance, equity curve metadata. | Direct replacement for `backtesting read-portfolio`. | read-only | `PortfolioSummaryService`, `PortfolioAnalyticsService`, `PortfolioEquityCurveService` | P0 |
| `platform dashboard-snapshot --format json --output artifacts/platform/dashboard.json` | Export dashboard/API validation snapshot. | Direct replacement for `backtesting read-dashboard`; dashboard validation is platform-level. | read-only artifact output | `PortfolioSummaryService`, `ActiveStrategiesService`, portfolio analytics/equity services | P1 |
| `risk verify-parameter-effects --controls ... --settings ... --symbols ... --start ... --end ...` | Verify risk parameters influence replay/runtime behavior. | Direct replacement for `backtesting verify-risk-parameter-effects`. | platform-level/local-mutating | Existing handler logic, replay runtime service, risk/portfolio settings services | P0 |
| `operations verify-notification-events --controls ... --settings ...` | Verify notification flags and event emission. | Direct replacement for `backtesting verify-notification-events`. | local-mutating verification | `RuntimeControlService`, `AutoPromotionService`, runtime job failure path, audit log repo | P1 |
| `governance verify-allocation --controls ... --settings ... --total-capital ...` | Verify governance state, promotion rules, allocation policy, overrides, and audit interactions. | Direct replacement for `backtesting verify-governance-allocation`. | local-mutating verification | `StrategyGovernanceService`, `PortfolioEngine`, promotion rules/allocation repos | P0 |
| `governance verify-auto-promotion --settings fixtures/settings.yaml` | Verify auto-promotion gating, source-of-truth settings, audit records, notification event. | Direct replacement for `backtesting verify-auto-promotion`. | local-mutating/transient verification | `AutoPromotionService` | P0 |
| `governance verify-auto-demotion --settings fixtures/settings.yaml` | Verify demotion gating, idempotency, audit records, notification event. | Direct replacement for `backtesting verify-auto-demotion`. | local-mutating/transient verification | `AutoDemotionService` | P0 |

## 4. Testing Plan

### Phase 0: Help Commands

Current deprecated domain:

```powershell
python -m autonomous_trading_platform.cli.main backtesting --help
python -m autonomous_trading_platform.cli.main backtesting run --help
python -m autonomous_trading_platform.cli.main backtesting inspect-results --help
python -m autonomous_trading_platform.cli.main backtesting seed-fixture --help
python -m autonomous_trading_platform.cli.main backtesting seed-settings --help
python -m autonomous_trading_platform.cli.main backtesting read-settings --help
python -m autonomous_trading_platform.cli.main backtesting seed-controls --help
python -m autonomous_trading_platform.cli.main backtesting read-controls --help
python -m autonomous_trading_platform.cli.main backtesting read-portfolio --help
python -m autonomous_trading_platform.cli.main backtesting read-dashboard --help
python -m autonomous_trading_platform.cli.main backtesting verify-risk-parameter-effects --help
python -m autonomous_trading_platform.cli.main backtesting verify-notification-events --help
python -m autonomous_trading_platform.cli.main backtesting verify-governance-allocation --help
python -m autonomous_trading_platform.cli.main backtesting verify-auto-promotion --help
python -m autonomous_trading_platform.cli.main backtesting verify-auto-demotion --help
```

Replacement help checks:

```powershell
python -m autonomous_trading_platform.cli.main platform backtest --help
python -m autonomous_trading_platform.cli.main platform fixture seed --help
python -m autonomous_trading_platform.cli.main settings show --help
python -m autonomous_trading_platform.cli.main controls show --help
python -m autonomous_trading_platform.cli.main portfolio snapshot --help
python -m autonomous_trading_platform.cli.main risk verify-parameter-effects --help
python -m autonomous_trading_platform.cli.main operations verify-notification-events --help
python -m autonomous_trading_platform.cli.main governance verify-auto-promotion --help
python -m autonomous_trading_platform.cli.main governance verify-auto-demotion --help
```

### Phase 1: Safe Read-Only Commands

Current:

```powershell
python -m autonomous_trading_platform.cli.main backtesting run --timestamp 2026-05-26T15:30:00Z
python -m autonomous_trading_platform.cli.main backtesting inspect-results --run-id bt_20260526_001
python -m autonomous_trading_platform.cli.main backtesting seed-fixture --fixture fixtures/platform_replay.yaml --dry-run
python -m autonomous_trading_platform.cli.main backtesting read-settings
python -m autonomous_trading_platform.cli.main backtesting read-controls
python -m autonomous_trading_platform.cli.main backtesting read-portfolio
python -m autonomous_trading_platform.cli.main backtesting read-dashboard
```

Replacement:

```powershell
python -m autonomous_trading_platform.cli.main platform backtest plan --symbols SPY,QQQ --start 2026-01-01 --end 2026-03-31
python -m autonomous_trading_platform.cli.main platform fixture seed --fixture fixtures/platform_replay.yaml --dry-run
python -m autonomous_trading_platform.cli.main settings show --format json
python -m autonomous_trading_platform.cli.main controls show --format json
python -m autonomous_trading_platform.cli.main portfolio snapshot --format json
python -m autonomous_trading_platform.cli.main platform dashboard-snapshot --format json
```

### Phase 2: Local DB Mutation Commands

Run only against disposable local DB/data roots:

```powershell
python -m autonomous_trading_platform.cli.main backtesting seed-settings --config fixtures/settings.yaml
python -m autonomous_trading_platform.cli.main backtesting seed-controls --config fixtures/controls.yaml --clean
python -m autonomous_trading_platform.cli.main backtesting seed-fixture --fixture fixtures/platform_replay.yaml
```

Replacement:

```powershell
python -m autonomous_trading_platform.cli.main settings seed --config fixtures/settings.yaml --dry-run
python -m autonomous_trading_platform.cli.main settings seed --config fixtures/settings.yaml --actor local-operator --reason "seed local replay settings"
python -m autonomous_trading_platform.cli.main controls seed --config fixtures/controls.yaml --clean --dry-run
python -m autonomous_trading_platform.cli.main controls seed --config fixtures/controls.yaml --clean --actor local-operator --reason "seed local replay controls"
python -m autonomous_trading_platform.cli.main platform fixture seed --fixture fixtures/platform_replay.yaml --actor local-operator --reason "seed platform replay fixture"
```

### Phase 3: Cross-Domain / Runtime Commands

Current:

```powershell
python -m autonomous_trading_platform.cli.main backtesting verify-risk-parameter-effects --controls fixtures/controls.yaml --settings fixtures/settings.yaml --symbols SPY,QQQ --start 2026-01-01 --end 2026-03-31 --starting-cash 100000 --random-seed 42 --reset-sim-state --print-summary
python -m autonomous_trading_platform.cli.main backtesting verify-governance-allocation --controls fixtures/controls.yaml --settings fixtures/settings.yaml --total-capital 100000
python -m autonomous_trading_platform.cli.main backtesting verify-auto-promotion --settings fixtures/settings.yaml
python -m autonomous_trading_platform.cli.main backtesting verify-auto-demotion --settings fixtures/settings.yaml
```

Replacement:

```powershell
python -m autonomous_trading_platform.cli.main risk verify-parameter-effects --controls fixtures/controls.yaml --settings fixtures/settings.yaml --symbols SPY,QQQ --start 2026-01-01 --end 2026-03-31 --starting-cash 100000 --random-seed 42 --reset-sim-state --print-summary
python -m autonomous_trading_platform.cli.main governance verify-allocation --controls fixtures/controls.yaml --settings fixtures/settings.yaml --total-capital 100000
python -m autonomous_trading_platform.cli.main governance verify-auto-promotion --settings fixtures/settings.yaml
python -m autonomous_trading_platform.cli.main governance verify-auto-demotion --settings fixtures/settings.yaml
```

### Phase 4: Broker / External Commands

No current `backtesting` command should be broker/live external-facing. Keep replacement commands offline/local unless a future `platform backtest run` explicitly documents external data dependencies.

## 5. Risks / Suspicious Wiring

- The entire `backtesting` domain is misleading because most commands are settings, controls, portfolio, dashboard, risk, operations, and governance utilities.
- `backtesting run` and `backtesting inspect-results` are placeholders and should not be migrated as working commands.
- `seed-controls --clean` deletes strategy governance rows, strategy control states, and active allocation overrides without a dry-run or confirmation gate.
- `seed-settings` mutates operator settings but has no `--dry-run`, `--actor`, or `--reason`.
- `seed-controls` mutates runtime control state, strategy configs, governance, strategy toggles, and allocation overrides but has no `--dry-run`, `--actor`, or `--reason`.
- `seed-fixture` has `--dry-run`, but the mutating path crosses settings, controls, strategy, governance, and portfolio allocation boundaries without service-level audit logging.
- Several seed paths write directly through repositories/models rather than the higher-level services used by REST routes, so audit behavior may differ from operator/API flows.
- `read-dashboard` is named like a backtest utility but is actually a frontend/API state snapshot.
- Verification commands write artifacts under `artifacts/backtesting`; after migration, artifact paths should move to `artifacts/risk`, `artifacts/operations`, `artifacts/governance`, or `artifacts/platform`.
- Verification commands intentionally mutate/probe local state; their cleanup/rollback semantics should be explicit in command output and docs.
- `verify-notification-events` activates kill switch behavior as part of verification. It is local-service backed, but it still needs an obvious safety/fixture guard because the action name is operationally serious.
- Handler signatures match parser wiring; no parser/handler mismatch was found in registration.

## 6. Recommended Refactor / Extension

- Deprecate `backtesting` as a CLI domain.
- Do not move `backtesting run` or `inspect-results` as-is; replace them with `platform backtest run/inspect/report` only when backed by a canonical implementation.
- Move fixture and dashboard workflow commands to `platform`.
- Move settings commands to `settings`.
- Move controls commands to `controls`, with future allocation override commands split to `portfolio` if needed.
- Move portfolio reads to `portfolio`.
- Move risk parameter verification to `risk`.
- Move notification verification to `operations`.
- Move governance/allocation and promotion/demotion verification to `governance`.
- Add `--dry-run`, `--actor`, `--reason`, `--output`, and JSON/artifact output controls consistently before removing the old names.
- Add audit logging parity by routing mutating replacements through application services instead of raw repositories where practical.
- Keep temporary deprecated wrappers only long enough to print the new command path and forward execution.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `backtesting run` | Placeholder | no | Low | Deprecate; replace with `platform backtest run` when implemented |
| `backtesting inspect-results` | Placeholder | no | Low | Deprecate; replace with `platform backtest inspect/report` |
| `backtesting seed-fixture` | Useful multi-domain fixture seeder | no | Medium | Move to `platform fixture seed`; keep `--dry-run`; add actor/reason/audit |
| `backtesting seed-settings` | Useful settings fixture writer | no | Medium | Move to `settings seed`; add `--dry-run`, actor, reason |
| `backtesting read-settings` | Useful read-only settings inspection | no | Low | Move to `settings show` |
| `backtesting seed-controls` | Useful but broad control/governance/allocation seeder | no | High | Move to `controls seed`; add dry-run/confirmation for `--clean` |
| `backtesting read-controls` | Useful read-only controls snapshot | no | Low | Move to `controls show` |
| `backtesting read-portfolio` | Useful portfolio API-equivalent snapshot | no | Low | Move to `portfolio snapshot` |
| `backtesting read-dashboard` | Useful frontend/dashboard snapshot | no | Low | Move to `platform dashboard-snapshot` |
| `backtesting verify-risk-parameter-effects` | Useful risk wiring verifier | no | Medium | Move to `risk verify-parameter-effects`; update artifact path |
| `backtesting verify-notification-events` | Useful notification verifier | no | Medium | Move to `operations verify-notification-events`; add safety/fixture guard |
| `backtesting verify-governance-allocation` | Useful governance/allocation verifier | no | Medium | Move to `governance verify-allocation`; preserve artifact output |
| `backtesting verify-auto-promotion` | Useful promotion automation verifier | no | Medium | Move to `governance verify-auto-promotion`; document rollback/artifact |
| `backtesting verify-auto-demotion` | Useful demotion automation verifier | no | Medium | Move to `governance verify-auto-demotion`; document rollback/artifact |
