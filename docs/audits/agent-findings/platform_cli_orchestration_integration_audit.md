# Platform CLI Orchestration Integration Audit

## Executive Summary

This redo uses `docs/backend/cli/cli.md` as the canonical CLI source and checks
it against the current CLI and orchestration code. The active CLI surface is 85
leaf commands across 12 top-level domains:

- `admin`
- `backtesting`
- `diagnostics`
- `execution`
- `features`
- `ingestion`
- `operations`
- `research`
- `runtime`
- `safety`
- `strategy`
- `universe`

The platform already has several useful domain runners and some cross-domain
orchestrators, especially under `runtime`, `backtesting`, `research`, `features`,
and `universe`. It does not yet expose one canonical full-platform historical
backtest command that runs universe selection, ingestion, features, trading,
portfolio state, governance controls, verification, and dashboard-ready artifacts
as one audited flow.

The strongest near-term path is to canonize `docs/backend/cli/cli.md` as the
command inventory, then build a test TODO list around command categories:
help/discovery, read-only inspection, dry-run/domain validation, local mutating
database flows, cross-domain replay flows, and broker/paper flows.

## Current CLI / Domain Coverage Matrix

| Domain | Existing CLI Commands | Backend Services / Code Touched | Combined Orchestrators | Current Status | Gaps |
|---|---|---|---|---|---|
| Universe | `universe select-now`, `inspect-active`, `inspect-symbols`, `inspect-symbol`, `validate-active`, `validation-report`, `inspect-ingestion-input`, `seed`, `raw-pool-refresh`, `raw-pool-inspect`, `raw-pool-inspect-symbol`, `candidate-generate`, `candidate-inspect`, `candidate-inspect-rejections`, `candidate-inspect-symbol`, `history`, `runtime-status`, `observability-status`, `propose-rebalance`, `rotate`, `rollback`, `rotation-history`, `rebalance-history`, `rotation-status`, `history-for-date`, `replay-timeline`, `compare-universes`, `symbol-history` | `src/autonomous_trading_platform/cli/commands/universe.py`; universe selection/versioning services through handlers | Universe candidate generation, rebalance proposal, rotation, rollback, history replay | Standalone CLI exists; partially cross-wired to ingestion/runtime/observability | Needs tested operator runbook for safe seed -> candidate -> dry-run rebalance -> rotate -> rollback flow. |
| Ingestion | `ingestion run-bars`, `run-backfill`, `run-corporate-actions`, `inspect-bar` | `src/autonomous_trading_platform/cli/commands/ingestion.py`; ingestion cycles and repositories | `runtime replay-ingestion` can run ingestion over a historical window | Standalone CLI exists; cross-domain replay exists | No single platform command chooses universe, runs ingestion, then features/trading by default. |
| Features | `features run-pipeline` | `src/autonomous_trading_platform/cli/commands/features.py`; feature dataset/version pipeline | `runtime replay`, `runtime replay-debug`, `runtime replay-ingestion` can include feature cycles | Standalone CLI exists; partially wired into replay | CLI has one broad command; needs tested prerequisites for dataset version, adjusted/raw price basis, and feature-set flags. |
| Research | `research run-simulation`, `run-experiment`, `list-strategy-types`, `inspect-strategy`, `list-components`, `inspect-component`, `generate-strategies`, `summarize-generated-configs`, `inspect-checkpoints`, `plan-restart`, `resume-experiment` | `src/autonomous_trading_platform/cli/commands/research.py`; research registry, experiment orchestration, checkpoints | `research run-experiment`; `runtime soak-loop research` | Standalone CLI exists; some commands are orchestration entrypoints | `research run-simulation` is direct/ad-hoc and should not be treated as the canonical experiment orchestrator without verification. |
| Simulation / Backtesting | `backtesting run`, `inspect-results`, `seed-fixture`, `seed-settings`, `read-settings`, `seed-controls`, `read-controls`, `read-portfolio`, `read-dashboard`, `verify-risk-parameter-effects`, `verify-notification-events`, `verify-governance-allocation`, `verify-auto-promotion`, `verify-auto-demotion`; `runtime soak-loop backtest`; `runtime replay`; `runtime replay-debug` | `src/autonomous_trading_platform/cli/commands/backtesting.py`; `src/autonomous_trading_platform/runtime/replay_debug.py`; `src/autonomous_trading_platform/runtime/services/replay_runtime_service.py` | Multiple verification commands and runtime replay flows | Combined orchestration exists, but fragmented | `backtesting run` implementation status needs manual testing; no single canonical full historical platform backtest command. |
| Trading Cycle / Runtime Scheduling | `runtime run-cycle`, `runtime trigger-job`, `runtime inspect-manifest`, `runtime inspect-audit`, `runtime replay`, `runtime replay-debug`, `runtime replay-ingestion`, `runtime soak-loop backtest`, `runtime soak-loop paper`, `runtime soak-loop research` | `src/autonomous_trading_platform/cli/commands/runtime.py`; scheduler cycles; manual trigger service; run manifests/audit | `runtime run-cycle`, `runtime trigger-job`, replay and soak-loop commands | Combined orchestration exists | `runtime trigger-job` dispatches only `trading_cycle`, `strategy_auto_promotion_cycle`, `strategy_auto_demotion_cycle`, and `strategy_allocation_rebalance_cycle`; registry also lists ingestion, feature, corporate-action, and experiment jobs. |
| Execution | `execution reconcile-order`, `reconcile-open-orders`, `inspect-order`, `inspect-position`, `inspect-cash` | `src/autonomous_trading_platform/cli/commands/execution.py`; broker/order/cash repositories | Reconciliation commands touch broker/order state | Standalone CLI exists | No execution CLI for simulated fills as a standalone test harness; broker-facing commands need environment guard verification. |
| Broker / Paper Trading | `runtime soak-loop paper`; execution reconciliation commands | `src/autonomous_trading_platform/cli/commands/runtime_soak_loop.py`; broker clients; reconciliation paths | Paper soak loop | Partially wired | Needs explicit paper-only runbook and preflight checks before broad testing. |
| Portfolio Allocation | `backtesting read-portfolio`, `read-dashboard`, `verify-governance-allocation`; `runtime trigger-job --job-name strategy_allocation_rebalance_cycle` | allocation services and governance/rebalance cycles | Backtesting verification and scheduler trigger | Partially wired | Allocation override is REST/service-backed but has no direct CLI. |
| Governance / Promotion / Demotion | `backtesting verify-auto-promotion`, `verify-auto-demotion`; `runtime trigger-job --job-name strategy_auto_promotion_cycle`; `runtime trigger-job --job-name strategy_auto_demotion_cycle` | governance cycle services; strategy governance REST route | Verification and scheduler triggers | Partially wired | Manual approve/reject/force demotion exists via REST governance transition, not CLI. |
| Risk / Safety Controls | `safety arm-live`, `disarm-live`, `enable-kill-switch`, `disable-kill-switch`, `gate-status`; `backtesting verify-risk-parameter-effects` | `src/autonomous_trading_platform/cli/commands/safety.py`; `RuntimeControlService`; kill switch repositories; runtime guards | Safety CLI plus verification command | Standalone safety CLI exists; partially wired to runtime controls | Pause/resume and trading-mode controls are REST/service-backed but CLI-missing. Audit coverage of all mutating CLI commands needs verification. |
| Operator Settings / Runtime Controls | `backtesting seed-settings`, `read-settings`, `seed-controls`, `read-controls`; no direct CLI for pause/resume/trading-mode/strategy-toggle/allocation override | REST routes under `/api/v1/controls`, `/api/v1/system/trading-mode`, `/api/v1/strategies/{id}/enabled`, `/api/v1/strategies/{id}/allocation`, `/api/v1/settings` | Backtesting seed/read commands support local verification | Service exists but no direct CLI for key operator controls | CLI gap for simulating operator changes during a run. |
| Observability / Runtime Soak | `operations verify-runtime-soak`, `diagnostics snapshot`, `admin inspect-config`, `admin inspect-env`, `admin inspect-failed-runs`, `runtime inspect-manifest`, `runtime inspect-audit`, `universe observability-status` | operations service, runtime snapshot, manifests, audit logs | Runtime soak verification command | Diagnostic CLI exists | No single command bundles replay outputs, manifests, audits, verifier, and dashboard JSON into a result artifact directory. |
| REST API / Dashboard Outputs | `backtesting read-dashboard`, `backtesting read-portfolio`; no generic dashboard export CLI | REST routes under `/portfolio`, `/strategies`, `/experiments`, `/operations`, `/controls`, `/settings`, `/audit-log`, `/metadata` | Dashboard read commands for backtesting state | Partially wired | Need artifact contract for platform backtest result JSON and dashboard-ready bundle. |

## Canonical CLI Inventory By Domain

The command names below are the canonical index-level inventory from
`docs/backend/cli/cli.md`. Use `python -m autonomous_trading_platform.cli.main`
as the code entrypoint unless a local environment supplies an external `atp`
console alias.

| Domain | Commands | Test Category |
|---|---|---|
| `admin` | `inspect-config`, `inspect-env`, `inspect-failed-runs` | Read-only inspection |
| `diagnostics` | `snapshot` | Read-only diagnostics |
| `safety` | `arm-live`, `disarm-live`, `enable-kill-switch`, `disable-kill-switch`, `gate-status` | Safety-critical; inspect first, mutate only in isolated local DB |
| `ingestion` | `run-bars`, `run-backfill`, `run-corporate-actions`, `inspect-bar` | Mutating ingestion plus read inspection |
| `features` | `run-pipeline` | Mutating feature dataset path |
| `strategy` | `evaluate-bar`, `inspect-readiness` | Strategy runtime evaluation/readiness |
| `research` | `run-simulation`, `run-experiment`, `list-strategy-types`, `inspect-strategy`, `list-components`, `inspect-component`, `generate-strategies`, `summarize-generated-configs`, `inspect-checkpoints`, `plan-restart`, `resume-experiment` | Mixed read, artifact-writing, experiment orchestration |
| `backtesting` | `run`, `inspect-results`, `seed-fixture`, `seed-settings`, `read-settings`, `seed-controls`, `read-controls`, `read-portfolio`, `read-dashboard`, `verify-risk-parameter-effects`, `verify-notification-events`, `verify-governance-allocation`, `verify-auto-promotion`, `verify-auto-demotion` | Simulation, seed/read, verification |
| `runtime` | `run-cycle`, `trigger-job`, `inspect-manifest`, `inspect-audit`, `soak-loop backtest`, `soak-loop paper`, `soak-loop research`, `replay`, `replay-debug`, `replay-ingestion` | Cross-domain runtime and replay orchestration |
| `execution` | `reconcile-order`, `reconcile-open-orders`, `inspect-order`, `inspect-position`, `inspect-cash` | Broker/order inspection and reconciliation |
| `operations` | `verify-runtime-soak` | Verification |
| `universe` | `select-now`, `inspect-active`, `inspect-symbols`, `inspect-symbol`, `validate-active`, `validation-report`, `inspect-ingestion-input`, `seed`, `raw-pool-refresh`, `raw-pool-inspect`, `raw-pool-inspect-symbol`, `candidate-generate`, `candidate-inspect`, `candidate-inspect-rejections`, `candidate-inspect-symbol`, `history`, `runtime-status`, `observability-status`, `propose-rebalance`, `rotate`, `rollback`, `rotation-history`, `rebalance-history`, `rotation-status`, `history-for-date`, `replay-timeline`, `compare-universes`, `symbol-history` | Universe lifecycle, validation, rotation, history |

## Existing Combined Orchestrators

| Command | Scope | Already Wired | Notes |
|---|---|---|---|
| `runtime run-cycle` | One trading cycle | Yes | Calls the trading cycle path. Should be treated as mutating runtime state. |
| `runtime trigger-job --job-name trading_cycle` | Manual scheduler trigger | Yes | Uses no-overlap manual trigger service. |
| `runtime trigger-job --job-name strategy_auto_promotion_cycle` | Governance promotion cycle | Yes | Runtime dispatcher includes this job. |
| `runtime trigger-job --job-name strategy_auto_demotion_cycle` | Governance demotion cycle | Yes | Runtime dispatcher includes this job. |
| `runtime trigger-job --job-name strategy_allocation_rebalance_cycle` | Allocation rebalance cycle | Yes | Runtime dispatcher includes this job. |
| `runtime replay` | Historical runtime replay | Yes | Produces replay summary/artifacts; includes configurable cycles. |
| `runtime replay-debug` | Local deterministic runtime replay | Yes | Explicitly says it never submits broker orders; good first target for full-platform smoke tests. |
| `runtime replay-ingestion` | Historical ingestion replay | Yes | Can optionally run trading with `--run-trading`; default disables trading. |
| `runtime soak-loop backtest` | Historical soak | Yes | Useful for longer simulation checks. |
| `runtime soak-loop paper` | Paper soak | Yes | Broker-facing; should be tested after simulation paths. |
| `runtime soak-loop research` | Research soak | Yes | Crosses research and simulation. |
| `research run-experiment` | Experiment orchestration | Yes | Better canonical research orchestrator than direct `run-simulation`. |
| `features run-pipeline` | Feature generation | Yes | One domain pipeline command. |
| `backtesting verify-*` | Risk, notification, governance, promotion, demotion verification | Yes | Strong verification suite for controls/governance wiring. |
| `universe propose-rebalance`, `rotate`, `rollback` | Universe lifecycle orchestration | Yes | Use `--dry-run` first where available. |
| `operations verify-runtime-soak` | Runtime soak verifier | Yes | Verifies soak windows, not a full platform artifact bundle. |

## Cross-Cutting CLI Map

| Command / Area | Crosses Domains | Why It Is Cross-Cutting | Test Priority |
|---|---|---|---|
| `runtime replay-debug` | Runtime, ingestion/backfill assumptions, features, trading, portfolio, controls | Reads persisted settings/control state and exercises local replay without broker orders. | Highest |
| `runtime replay` | Runtime, features, trading, rebalance, portfolio snapshot | Persists runtime replay evidence and can produce JSON output. | Highest |
| `runtime replay-ingestion --run-trading` | Ingestion, features, trading runtime | Replays historical ingestion ticks and can invoke trading. | High, after no-trading replay-ingestion |
| `runtime trigger-job` | Runtime scheduler, trading, governance, allocation | Manually triggers selected scheduled jobs. | High |
| `backtesting verify-risk-parameter-effects` | Risk controls, settings, simulation, trading | Seeds controls/settings and verifies behavioral effects. | High |
| `backtesting verify-governance-allocation` | Governance, allocation, portfolio | Verifies allocation and governance interactions. | High |
| `backtesting verify-auto-promotion` / `verify-auto-demotion` | Governance automation, controls, audit | Tests promotion/demotion automation behavior. | High |
| `backtesting verify-notification-events` | Operations alerts, kill switch, drawdown, governance events | Verifies notification event behavior. | Medium |
| `features run-pipeline` | Storage lineage, features, research/simulation inputs | Produces feature data needed by research and simulation. | High |
| `research run-experiment` | Research, simulation, strategy registry, checkpoints | Runs experiment pipeline and writes artifacts/state. | Medium |
| `universe inspect-ingestion-input` | Universe, ingestion | Shows what universe would feed ingestion. | High |
| `universe runtime-status` | Universe, runtime | Checks runtime universe state. | Medium |
| `universe observability-status` | Universe, observability | Checks universe observability status. | Medium |
| `universe propose-rebalance` / `rotate` / `rollback` | Universe, governance/approval, runtime membership | Controls active universe membership lifecycle. | Medium; dry-run first |
| `safety enable-kill-switch` / `disable-kill-switch` | Safety, runtime controls, execution guard | Changes kill-switch state that runtime/execution should honor. | High in isolated local DB; dangerous elsewhere |
| `execution reconcile-open-orders` | Execution, broker state, runtime recovery | Mutates local order state based on broker reconciliation. | Low until broker sandbox is prepared |
| `operations verify-runtime-soak` | Observability, runtime evidence | Verifies long-run evidence windows. | Medium |
| `admin inspect-*` / `diagnostics snapshot` | Operations, config, runtime diagnostics | Safe discovery commands for environment and failed runs. | Highest |

## Isolated Domain Runners

These CLIs are useful but should not be mistaken for platform-wide orchestration:

- `admin inspect-config`, `admin inspect-env`, `admin inspect-failed-runs`
- `diagnostics snapshot`
- `execution inspect-order`, `execution inspect-position`, `execution inspect-cash`
- `ingestion inspect-bar`
- `research list-strategy-types`, `inspect-strategy`, `list-components`, `inspect-component`
- `research summarize-generated-configs`, `inspect-checkpoints`, `plan-restart`
- `strategy inspect-readiness`
- Universe inspect/history commands such as `inspect-active`, `history`,
  `history-for-date`, `replay-timeline`, `compare-universes`, and
  `symbol-history`

They belong in the rerun checklist, but they validate surfaces and state rather
than proving full platform flow.

## Domains With Services But Missing CLI Coverage

| Capability | Existing Service / API | Existing CLI | Missing Wiring |
|---|---|---|---|
| Pause trading | `RuntimeControlService.pause_trading`; `POST /api/v1/controls/pause` | None | No `runtime pause` or `safety pause-trading` CLI. |
| Resume trading | `RuntimeControlService.resume_trading`; `POST /api/v1/controls/resume` | `safety disable-kill-switch` is not the same full control path | No direct resume CLI with rationale/user audit. |
| Trading mode switch | `RuntimeControlService.update_trading_mode`; `PUT /api/v1/system/trading-mode` | `safety arm-live` / `disarm-live` only cover live gate state | No CLI for `simulation` / `paper` / `live` mode transitions. |
| Strategy enable/disable | `StrategyControlService.set_enabled`; `PUT /api/v1/strategies/{strategy_id}/enabled` | No direct CLI | Cannot simulate operator strategy toggles from CLI without REST. |
| Allocation override | `StrategyAllocationService.override_allocation`; `PUT /api/v1/strategies/{strategy_id}/allocation` | No direct CLI; backtesting can seed/verify controls | No direct operator allocation override CLI. |
| Governance transition | `StrategyGovernanceService.transition`; `POST /api/v1/strategies/{strategy_id}/governance/transition` | Verification CLIs only | No direct approve/reject/force-demotion CLI. |
| Operator settings update | `PUT /api/v1/settings` | `backtesting seed-settings` / `read-settings` for local verification | No direct settings update CLI for operator workflows. |
| Operations alert actions | `/api/v1/operations/alerts/*` | No direct CLI | Acknowledge/resolve/snooze/note are REST-only. |
| REST dashboard artifact export | `/api/v1/portfolio/*`, `/api/v1/strategies/*`, `/api/v1/operations/*`, `/api/v1/audit-log` | `backtesting read-dashboard`, `read-portfolio` only | No platform report/export CLI that bundles dashboard-ready JSON. |

## Deferred Control Task Status

| Task | Status | Evidence | CLI Commands | REST Routes | Missing Wiring |
|---|---|---|---|---|---|
| TASK-248: Kill switches halt all trading activity or disable individual strategies immediately; integrate with execution engine. | Partially implemented | `RuntimeControlService.activate_kill_switch`; `safety enable-kill-switch`; runtime/replay guards inspect kill-switch/control state; order cancellation is attempted in service path. | `safety enable-kill-switch`, `safety disable-kill-switch`, `safety gate-status`; `backtesting verify-notification-events` exercises kill-switch notification behavior. | `POST /api/v1/controls/kill-switch`, `GET /api/v1/controls/state` | Per-strategy disable is REST-backed, not CLI-backed. Immediate effect across every live/paper loop and execution submission path needs end-to-end verification. |
| TASK-249: Strategy toggles enabling/disabling strategies without redeploying. | Partially implemented | `StrategyControlService.set_enabled`; trading-cycle code checks strategy control state. | No direct CLI; `backtesting seed-controls`, `read-controls` can seed/read local state. | `PUT /api/v1/strategies/{strategy_id}/enabled`, `GET /api/v1/controls/state` | No operator CLI for toggles; runtime effect should be tested during replay and cycle execution. |
| TASK-250: Allocation override functionality for manual capital/position adjustments. | Partially implemented | `StrategyAllocationService.override_allocation`; allocation budget validation; backtesting governance/allocation verifier. | `backtesting seed-controls`, `read-controls`, `verify-governance-allocation`, `read-portfolio`, `read-dashboard` | `PUT /api/v1/strategies/{strategy_id}/allocation`, portfolio allocation routes | No direct allocation override CLI. Manual position adjustment is unclear and needs code verification before documenting as supported. |
| TASK-251: Environment controls for simulation, paper, and live trading modes. | Partially implemented | `RuntimeControlService.update_trading_mode`; safety live arm/disarm commands; runtime guards check mode. | `safety arm-live`, `safety disarm-live`, `safety gate-status` | `PUT /api/v1/system/trading-mode`, `GET /api/v1/controls/state` | No CLI for trading-mode transition. Need verify every broker-facing CLI respects mode/live gates. |
| TASK-252: All control actions logged/auditable with user, timestamp, rationale. | Partially implemented | Runtime control, strategy toggle, allocation override, and trading-mode services record audit/operator actions. `runtime inspect-audit` and `/audit-log` expose audit data. | `runtime inspect-audit`; safety commands may update kill-switch state, but audit parity needs verification. | `GET /api/v1/audit-log`; control/settings/strategy routes use actor/rationale patterns | Need audit coverage review for every mutating CLI, especially ingestion, universe rotation, backtesting seed, and execution reconcile commands. |
| TASK-515: Multi-hour paper soak runbook/checklist using the same verifier. | Partially implemented | `runtime soak-loop paper`; `operations verify-runtime-soak`; docs under operations/runbooks and observability. | `runtime soak-loop paper`, `operations verify-runtime-soak`, `diagnostics snapshot`, execution inspect commands | Operations runtime-state/jobs routes support monitoring | Needs a validated checklist tying paper soak start, monitoring, verifier, reconciliation, and artifact capture into one runbook. |

## Operator Control / User Simulation Capability

| Action | Existing API | Existing CLI | Backend Service | Audit Logging | Runtime Effect | Gap |
|---|---|---|---|---|---|---|
| Pause trading | `POST /api/v1/controls/pause` | None | `RuntimeControlService.pause_trading` | Yes in service path | Runtime control state should stop trading paths that honor pause | No CLI; replay/cycle behavior needs test. |
| Resume trading | `POST /api/v1/controls/resume` | None | `RuntimeControlService.resume_trading` | Yes in service path | Clears pause or kill switch depending current state | No CLI; semantics differ from `safety disable-kill-switch`. |
| Enable kill switch | `POST /api/v1/controls/kill-switch` | `safety enable-kill-switch` | REST uses `RuntimeControlService`; CLI uses kill-switch service path | REST path logs operator action; CLI audit parity needs verification | Should block trading and cancel open orders in REST service path | Verify CLI and REST paths update the same state/audit tables. |
| Disable kill switch | Resume route can release kill switch; CLI has `safety disable-kill-switch` | `safety disable-kill-switch` | Runtime control service and kill-switch service paths | REST logs resume; CLI audit parity needs verification | Permits trading only if other gates pass | Need canonical operator semantics for resume vs disable kill switch. |
| Disable strategy | `PUT /api/v1/strategies/{strategy_id}/enabled` | None | `StrategyControlService.set_enabled` | Yes in service path | Trading cycle checks strategy control state | No CLI. |
| Enable strategy | `PUT /api/v1/strategies/{strategy_id}/enabled` | None | `StrategyControlService.set_enabled` | Yes in service path | Trading cycle should include enabled strategies if otherwise eligible | No CLI. |
| Change allocation override | `PUT /api/v1/strategies/{strategy_id}/allocation` | None | `StrategyAllocationService.override_allocation` | Yes in service path | Allocation services and dashboard routes should reflect override | No CLI; no manual position adjustment CLI found. |
| Switch environment / trading mode | `PUT /api/v1/system/trading-mode` | Partial: `safety arm-live`, `safety disarm-live` | `RuntimeControlService.update_trading_mode` | Yes in service path | Runtime guard should enforce mode | No CLI for `simulation`/`paper`/`live` mode transitions. |
| Approve promotion | `POST /api/v1/strategies/{strategy_id}/governance/transition` | Verification only: `backtesting verify-auto-promotion`; scheduler trigger | `StrategyGovernanceService.transition`; promotion cycle | Service path should audit | Governance state affects active strategy eligibility | No direct CLI for manual approval/rejection. |
| Reject promotion | Same transition route, depending supported states | None | `StrategyGovernanceService.transition` | Service path should audit | Governance state changes | Needs route-state verification and CLI gap remains. |
| Force demotion | Same transition route; auto-demotion verifier exists | `backtesting verify-auto-demotion`; scheduler trigger | `StrategyGovernanceService.transition`; demotion cycle | Service path should audit | Governance state changes and may affect allocation/runtime | No direct CLI for manual force demotion. |

## Full Platform Historical Backtest Feasibility

### What Already Exists

- Universe lifecycle CLIs can seed, inspect, generate candidates, dry-run/execute
  rebalance, rotate, rollback, and replay membership history.
- Ingestion CLIs can run bars, backfills, corporate actions, and inspect bars.
- Feature CLI can run a feature pipeline for dataset versions, symbols, windows,
  and price basis.
- Research CLIs can run experiments, simulations, strategy generation,
  component inspection, and checkpoint restart planning/resume.
- Runtime replay CLIs can execute local historical runtime paths and produce JSON
  output.
- Backtesting verification CLIs cover risk parameter effects, notification
  events, governance allocation, auto-promotion, and auto-demotion.
- Portfolio/dashboard read CLIs exist under `backtesting`.
- Runtime manifest/audit inspection and runtime soak verification CLIs exist.

### What Is Missing

- A single platform command that binds the whole flow into one run ID and
  artifact bundle.
- CLI coverage for several operator controls that currently exist only as REST
  routes/services.
- A canonical result JSON contract for dashboard-ready historical platform test
  output.
- A CLI-visible scheduler trigger for every manual-trigger-enabled registry job.
  The registry includes `market_ingestion_cycle`, `feature_pipeline_cycle`,
  `corporate_action_ingestion_cycle`, and `experiment_pipeline_cycle`, but
  `runtime trigger-job` currently dispatches only trading, promotion, demotion,
  and allocation rebalance jobs.
- Verified audit parity for all mutating CLI paths.

### What Should Not Be Included Yet

- Live trading.
- Broker-facing paper soak as the first full-platform test target.
- Manual operator control simulation through REST and CLI mixed together until
  the expected audit/runtime effects are documented.
- Large multi-year or all-symbol historical tests before a short fixed-universe
  test is deterministic and repeatable.

### What Should Be Mocked Or Simulated First

- Broker fills and order submission.
- Operator identity/authorization for local control simulation.
- Dashboard consumers, by validating JSON contract files instead of requiring a
  frontend session.
- External market-data availability, by using a small known local historical
  fixture or already-ingested data.

### What Should Be Verified Before Trusting Results

- The selected universe is deterministic for the test timestamp/window.
- Ingestion and feature versions are recorded and linked to the run.
- Runtime controls and strategy controls are captured at the start of the run.
- Simulated fills are distinguishable from paper/live broker state.
- Portfolio/equity outputs reconcile to fills and starting cash.
- Governance/allocation changes are auditable.
- Result JSON includes enough IDs for dashboard drill-through.
- All mutating commands are run against an isolated local database.

## Recommended Future Full-Platform CLI Shape

Do not implement these yet; this is the proposed shape for a future task list.

| Proposed Command | Purpose |
|---|---|
| `atp platform backtest plan` | Validate inputs and show the universe/data/features/runtime plan without mutation. |
| `atp platform backtest run` | Run the minimum full-platform historical flow and emit an artifact bundle. |
| `atp platform backtest verify` | Verify artifact bundle consistency, controls, manifests, audit logs, portfolio outputs, and dashboard JSON. |
| `atp platform backtest report` | Render a compact report from an existing artifact bundle. |
| `atp platform scenario list` | List predefined local scenarios. |
| `atp platform scenario validate` | Validate scenario prerequisites. |
| `atp platform scenario run` | Run a named scenario through the same backtest runner. |

Lower-level command additions to consider only after the platform shape is
agreed:

- `atp controls pause`
- `atp controls resume`
- `atp controls set-mode`
- `atp strategy enable`
- `atp strategy disable`
- `atp portfolio override-allocation`
- `atp governance transition`

Those may also live under existing domains (`runtime`, `safety`, `strategy`,
`portfolio-governance`) if preserving the current top-level command structure is
preferred.

## Minimum Viable Platform Test Flow

1. Use a fixed small universe, preferably seeded with `universe seed`.
2. Confirm the active universe with `universe inspect-active`,
   `universe inspect-symbols`, and `universe validate-active`.
3. Load or reuse existing historical data with `ingestion run-backfill`; inspect
   at least one bar with `ingestion inspect-bar`.
4. Generate features with `features run-pipeline`.
5. Run a short deterministic historical runtime replay with `runtime replay-debug`
   first, then `runtime replay` once debug replay is stable.
6. Include simulated fills only; do not include broker/paper submission in the
   first pass.
7. Read portfolio/dashboard state with `backtesting read-portfolio` and
   `backtesting read-dashboard`.
8. Run targeted verifiers: `backtesting verify-risk-parameter-effects`,
   `verify-governance-allocation`, `verify-auto-promotion`, and
   `verify-auto-demotion`.
9. Inspect runtime evidence with `runtime inspect-manifest`, `runtime inspect-audit`,
   `diagnostics snapshot`, and `operations verify-runtime-soak` where applicable.
10. Emit one artifact directory containing inputs, command transcript, run IDs,
    manifest/audit data, replay summary JSON, portfolio JSON, dashboard JSON, and
    verification results.

## CLI Rerun TODO List

### Phase 0: Discovery / Help Smoke Tests

Run these first because they should not mutate state:

- `python -m autonomous_trading_platform.cli.main --help`
- `python -m autonomous_trading_platform.cli.main admin --help`
- `python -m autonomous_trading_platform.cli.main backtesting --help`
- `python -m autonomous_trading_platform.cli.main diagnostics --help`
- `python -m autonomous_trading_platform.cli.main execution --help`
- `python -m autonomous_trading_platform.cli.main features --help`
- `python -m autonomous_trading_platform.cli.main ingestion --help`
- `python -m autonomous_trading_platform.cli.main operations --help`
- `python -m autonomous_trading_platform.cli.main research --help`
- `python -m autonomous_trading_platform.cli.main runtime --help`
- `python -m autonomous_trading_platform.cli.main runtime soak-loop --help`
- `python -m autonomous_trading_platform.cli.main safety --help`
- `python -m autonomous_trading_platform.cli.main strategy --help`
- `python -m autonomous_trading_platform.cli.main universe --help`

Then run `--help` for every leaf command listed in `docs/backend/cli/cli.md`.

### Phase 1: Read-Only / Low-Risk Commands

- `admin inspect-config`
- `admin inspect-env`
- `admin inspect-failed-runs`
- `diagnostics snapshot`
- `safety gate-status`
- `strategy inspect-readiness`
- `execution inspect-order`
- `execution inspect-position`
- `execution inspect-cash`
- `ingestion inspect-bar`
- `runtime inspect-manifest`
- `runtime inspect-audit`
- `backtesting read-settings`
- `backtesting read-controls`
- `backtesting read-portfolio`
- `backtesting read-dashboard`
- `operations verify-runtime-soak`
- Research list/inspect commands.
- Universe inspect/validate/history/status commands.

### Phase 2: Dry-Run And Artifact-Only Commands

- `universe propose-rebalance --dry-run`
- `universe rotate --dry-run`
- `universe rollback --dry-run`
- `backtesting seed-fixture --dry-run`
- `research generate-strategies` without writing output first, then with a
  disposable `--output`.
- `research plan-restart`
- `research resume-experiment --dry-run`

### Phase 3: Local Mutating Database Commands

Only run these against an isolated local database with disposable state:

- `universe seed`
- `universe select-now`
- `universe raw-pool-refresh`
- `universe candidate-generate`
- `universe propose-rebalance`
- `universe rotate`
- `universe rollback`
- `ingestion run-bars`
- `ingestion run-backfill`
- `ingestion run-corporate-actions`
- `features run-pipeline`
- `backtesting seed-settings`
- `backtesting seed-controls`
- `backtesting seed-fixture`
- `safety arm-live`
- `safety disarm-live`
- `safety enable-kill-switch`
- `safety disable-kill-switch`

### Phase 4: Cross-Domain Replay And Verification

- `runtime replay-debug` with a short fixed universe/window.
- `runtime replay` with the same inputs after debug replay succeeds.
- `runtime replay-ingestion` without `--run-trading`.
- `runtime replay-ingestion --run-trading` only after no-trading replay-ingestion
  succeeds.
- `backtesting verify-risk-parameter-effects`.
- `backtesting verify-notification-events`.
- `backtesting verify-governance-allocation`.
- `backtesting verify-auto-promotion`.
- `backtesting verify-auto-demotion`.
- `runtime trigger-job` for each currently dispatched job:
  `trading_cycle`, `strategy_auto_promotion_cycle`,
  `strategy_auto_demotion_cycle`, `strategy_allocation_rebalance_cycle`.

### Phase 5: Broker / Paper-Facing Commands

Run these only after environment, broker sandbox credentials, trading mode,
live gates, and rollback procedures are verified:

- `runtime soak-loop paper`
- `execution reconcile-order`
- `execution reconcile-open-orders`

## Gaps Before Implementation

- Decide whether the canonical platform runner should live under a new
  `platform` domain or under existing `runtime` / `backtesting` domains.
- Decide the artifact bundle contract before implementing the runner.
- Add or document CLI parity for REST-only operator controls.
- Verify `backtesting run` and decide whether it is canonical, legacy, or a
  thin placeholder.
- Decide whether `runtime trigger-job` should dispatch every
  `manual_trigger_enabled=True` registry job.
- Define safety policy for commands that can touch broker/paper/live state.
- Add a maintained generated inventory check so future parser drift is visible.

## Suggested Implementation Phases

| Phase | Goal | Output |
|---|---|---|
| 1 | CLI test inventory | Checked-off help/read-only/dry-run matrix based on this audit and `docs/backend/cli/cli.md`. |
| 2 | Deterministic local replay | Short fixed-universe `runtime replay-debug` and `runtime replay` commands with saved JSON output. |
| 3 | Verification bundle | Backtesting verifier outputs collected beside replay artifacts. |
| 4 | Operator control simulation | Decide CLI vs REST control simulation and verify audit/runtime effects. |
| 5 | Platform runner design | Final command contract for `platform backtest run/verify/report`. |
| 6 | Paper soak runbook | Validated paper-only runbook using `runtime soak-loop paper` and `operations verify-runtime-soak`. |

## Open Questions

- Should future platform orchestration introduce a new `platform` top-level CLI
  group, or should it extend `runtime` and `backtesting`?
- Is `atp` intended to become a packaged console script, or should docs continue
  using `python -m autonomous_trading_platform.cli.main` as the canonical
  executable form?
- Is `backtesting run` still intended to be used, or should `runtime replay` /
  `runtime replay-debug` become the canonical historical platform test path?
- Should CLI operator-control commands be added for pause/resume/mode/toggle/
  allocation/governance, or should those remain REST/dashboard-only?
- Which artifact schema should the dashboard consume from a platform historical
  backtest?
- Should `runtime trigger-job` support every scheduler registry job marked
  `manual_trigger_enabled=True`?
