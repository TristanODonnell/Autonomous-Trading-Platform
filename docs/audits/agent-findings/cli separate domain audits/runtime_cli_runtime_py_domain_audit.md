# Runtime CLI Domain Audit: `runtime.py`

Target file: `src/autonomous_trading_platform/cli/commands/runtime.py`

Scope note: this audit treats `runtime` as engine orchestration and scheduler-like backend cycle coordination. End-to-end product workflows, simulated operator journeys, artifact bundles, and dashboard/API validation should migrate or be wrapped under `platform`.

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp runtime` | `--help` | argparse only | no | no | yes | PASS_HELP_ONLY |
| `atp runtime run-cycle` | `--timestamp` | `handle_run_cycle` | yes | conditional | no | BROKER_OR_EXTERNAL |
| `atp runtime trigger-job` | `--job-name` | `handle_trigger_job` | yes | conditional | no | CROSS_DOMAIN_RUNTIME |
| `atp runtime inspect-manifest` | `--run-id` | `handle_inspect_manifest` | no | no | yes | READ_ONLY_SAFE |
| `atp runtime inspect-audit` | `--run-id` | `handle_inspect_audit` | no | no | yes | READ_ONLY_SAFE |
| `atp runtime soak-loop` | `--help` | argparse only | no | no | yes | PASS_HELP_ONLY |
| `atp runtime soak-loop backtest` | `--symbols`, `--start`, `--end`, `--initial-capital`, `--strategy-id` | `handle_soak_loop_backtest` | yes | no | no | PLATFORM_WORKFLOW |
| `atp runtime soak-loop paper` | `--mode {fast,realistic,single}` | `handle_soak_loop_paper` | yes | yes | no | BROKER_OR_EXTERNAL |
| `atp runtime soak-loop research` | `--symbols`, `--start`, `--end`, `--loop`, `--experiment-plan` | `handle_soak_loop_research` | yes | conditional | no | PLATFORM_WORKFLOW |
| `atp runtime replay` | `--symbols`, `--start`, `--end`, `--starting-cash`, `--random-seed`, `--price-basis`, `--calendar-mode`, `--cycles`, `--reset-sim-state`, `--print-summary`, `--output-json`, `--cadence-minutes`, `--max-ticks` | `handle_replay` | yes | no | no | LOCAL_DB_MUTATION |
| `atp runtime replay-debug` | same as `replay` | `handle_replay_debug` | yes | no | no | LOCAL_DB_MUTATION |
| `atp runtime replay-ingestion` | `--symbols`, `--start`, `--end`, `--cadence-minutes`, `--include-non-market-hours`, `--session-open-buffer-minutes`, `--session-close-buffer-minutes`, `--max-ticks`, `--run-trading`, `--stop-on-failure`, `--print-summary`, `--output-json` | `handle_replay_ingestion` | yes | conditional | no | CROSS_DOMAIN_RUNTIME |

## 2. Domain Responsibility Check

| Command | Placement | Notes |
|---|---|---|
| `runtime run-cycle` | correctly placed | Core trading-cycle orchestration. Needs safer options before broad use. |
| `runtime trigger-job` | correctly placed, but incomplete | Manual scheduler trigger is runtime-owned. It only dispatches four of eight registry jobs. |
| `runtime inspect-manifest` | should be duplicated/wrapped elsewhere | Runtime provenance is relevant here; `diagnostics` or `admin` should also expose broader read-only inspection. |
| `runtime inspect-audit` | should be duplicated/wrapped elsewhere | Runtime run audit is useful here; full audit browsing belongs in `diagnostics`/`admin`. |
| `runtime soak-loop backtest` | should move to `platform` or `research` | It runs a larger historical product workflow, not just engine coordination. |
| `runtime soak-loop paper` | should move/wrap under `operations` or `platform` | It is operational soak and broker-facing paper workflow. Runtime can retain internal loop primitives. |
| `runtime soak-loop research` | should move to `platform` or `research` | It chains historical research golden-path orchestration. |
| `runtime replay` | correctly placed | Runtime replay evidence is engine orchestration. |
| `runtime replay-debug` | correctly placed | Deterministic local runtime wiring validation belongs here. |
| `runtime replay-ingestion` | should be duplicated/wrapped elsewhere | Runtime can own the orchestrator, but ingestion/platform should expose operator-facing wrappers. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why runtime | Class | Target service/function | Priority |
|---|---|---|---|---|---:|
| `atp runtime list-jobs` | Print scheduler registry jobs, cadence/cron, lock key, manual trigger support. | Makes orchestration registry testable without DB mutation. | read-only | `scheduler.registry.scheduler_registry.SCHEDULER_REGISTRY` | P0 |
| `atp runtime plan-job --job-name <name>` | Validate job exists and show dispatcher availability, lock key, expected side effects. | Required before safe manual triggering. | read-only | `SCHEDULER_REGISTRY` plus runtime dispatcher map | P0 |
| `atp runtime list-job-runs --job-name trading_cycle --limit 20` | Inspect recent runtime job runs. | Runtime job history is the core orchestration ledger. | read-only | `RuntimeJobRunRepository.list_by_job_name` or `OperationsService.list_job_runs` | P0 |
| `atp runtime inspect-job-run --job-run-id <uuid>` | Inspect one job run plus input/output summaries and child runs. | Debugs scheduler execution trees. | read-only | `RuntimeJobRunRepository.get_by_job_run_id`, `list_children`, `RuntimeJobRunStepRepository.list_by_job_run_id` | P0 |
| `atp runtime plan-cycle --timestamp <iso>` | Resolve trading window, active universe, controls, expected mode, and whether cycle would be blocked. | Lets operators test scheduler decisions without running the engine. | read-only | `build_trading_cycle_window`, `build_trading_cycle_dependencies`, `RuntimeControlService.get_cycle_block_reason` | P0 |
| `atp runtime run-cycle --dry-run --timestamp <iso>` | Exercise preflight and planning without order dispatch or DB writes beyond optional audit. | Existing `run-cycle` is high-risk and ignores `--timestamp`. | read-only or local-mutating if audit enabled | `run_trading_cycle` needs dry-run/preflight seam | P0 |
| `atp runtime trigger-job --dry-run --job-name <name>` | Check registry/dispatcher/lock readiness without running job. | Prevents accidental cycle execution. | read-only | `ManualTriggerService` preflight method or CLI-side registry validation | P0 |
| `atp runtime trigger-job --job-name market_ingestion_cycle` | Trigger all registry jobs that are manual-enabled. | Registry currently includes jobs the CLI cannot dispatch. | cross-domain | `run_market_ingestion_cycle`, `run_feature_pipeline_cycle`, `run_corporate_action_ingestion_cycle`, `run_experiment_pipeline_cycle` | P1 |
| `atp runtime rescue-orphans --cutoff-minutes 30 --dry-run` | List or mark stale `running` jobs as failed/skipped. | Orphan recovery is runtime job ledger maintenance. | local-mutating | `OrphanJobRecoveryService.rescue_orphan_running_jobs` | P1 |
| `atp runtime calendar-status --timestamp <iso>` | Show market phase, next open/close, EOD eligibility. | Scheduler behavior depends on runtime clock/calendar. | read-only | `RealMarketCalendar`, `RealTradingClock` | P1 |
| `atp runtime replay-plan ...` | Validate replay symbols/date/cycles and summarize intended writes. | Makes replay safer and scriptable. | read-only | `expand_cycles`, `validate_cycles`, `load_settings_snapshot` | P1 |
| `atp runtime inspect-replay --replay-id <id>` | Load replay summary/job evidence after `replay` or `replay-debug`. | Replays are runtime artifacts. | read-only | `runtime_job_runs`, replay metadata in job output/audit logs | P2 |
| `atp runtime emit-artifact-bundle --job-run-id <uuid> --output <dir>` | Export manifest, audit logs, job tree, summaries as files. | Runtime should produce minimal engine evidence; full platform bundles belong in `platform`. | read-only artifact output | repositories for manifests/audit/job runs/steps | P2 |

## 4. Testing Plan

Phase 0: help only

```powershell
atp runtime --help
atp runtime run-cycle --help
atp runtime trigger-job --help
atp runtime inspect-manifest --help
atp runtime inspect-audit --help
atp runtime replay --help
atp runtime replay-debug --help
atp runtime replay-ingestion --help
atp runtime soak-loop --help
atp runtime soak-loop backtest --help
atp runtime soak-loop paper --help
atp runtime soak-loop research --help
```

Phase 1: safe read-only commands

```powershell
atp runtime inspect-manifest --run-id 00000000-0000-0000-0000-000000000000
atp runtime inspect-audit --run-id 00000000-0000-0000-0000-000000000000
```

Recommended after extension:

```powershell
atp runtime list-jobs
atp runtime plan-job --job-name trading_cycle
atp runtime list-job-runs --job-name trading_cycle --limit 20
atp runtime inspect-job-run --job-run-id 00000000-0000-0000-0000-000000000000
atp runtime plan-cycle --timestamp 2026-05-08T15:00:00Z
atp runtime calendar-status --timestamp 2026-05-08T15:00:00Z
```

Phase 2: local DB mutation commands

```powershell
atp runtime replay-debug --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --cycles runtime_checks --max-ticks 2 --print-summary
atp runtime replay --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --cycles market_backfill,features,trading --max-ticks 2 --output-json artifacts/runtime/replay-summary.json
atp runtime soak-loop backtest --symbols AAPL,MSFT --start 2026-05-04T00:00:00Z --end 2026-05-08T00:00:00Z --initial-capital 100000 --strategy-id baseline_strategy
```

Phase 3: cross-domain/runtime commands

```powershell
atp runtime replay-ingestion --symbols AAPL,MSFT --start 2026-05-04T13:30:00Z --end 2026-05-04T14:30:00Z --cadence-minutes 5 --max-ticks 3 --stop-on-failure --output-json artifacts/runtime/replay-ingestion-summary.json
atp runtime replay-ingestion --symbols AAPL,MSFT --start 2026-05-04T13:30:00Z --end 2026-05-04T14:30:00Z --cadence-minutes 5 --max-ticks 3 --run-trading --stop-on-failure
atp runtime trigger-job --job-name strategy_allocation_rebalance_cycle
atp runtime trigger-job --job-name strategy_auto_promotion_cycle
atp runtime trigger-job --job-name strategy_auto_demotion_cycle
```

Phase 4: broker/external commands

```powershell
atp runtime run-cycle --timestamp 2026-05-08T15:00:00Z
atp runtime trigger-job --job-name trading_cycle
atp runtime soak-loop paper --mode single
```

Run Phase 4 only in paper/sandbox mode with live trading gates verified.

## 5. Risks / Suspicious Wiring

- `run-cycle` registers `--timestamp` but `handle_run_cycle` ignores it and calls `run_trading_cycle()` with no timestamp.
- `run-cycle` can reach order submission/reconciliation depending environment and controls. It has no `--dry-run`, no explicit paper/live confirmation, and no local-only guard in the CLI.
- `trigger-job` validates against the full scheduler registry, but the dispatcher map only supports `trading_cycle`, `strategy_auto_promotion_cycle`, `strategy_auto_demotion_cycle`, and `strategy_allocation_rebalance_cycle`. Registry jobs for market ingestion, features, corporate actions, and experiments are manual-enabled but not triggerable here.
- `trigger-job` uses `InMemoryNoOverlapLock`, which only protects one process. It is not a durable scheduler lock for concurrent CLI/process use.
- `inspect-manifest` passes `args.run_id` as a string to a repository typed as `UUID`; depending database/driver behavior this may fail or silently miss rows.
- `inspect-manifest` and `inspect-audit` build full trading-cycle dependencies just to read audit data. That may instantiate broker/config-heavy dependencies unnecessarily.
- `replay` and `replay-debug` mutate local DB state and can reset simulated state via `--reset-sim-state`; neither command name makes the DB write blast radius obvious.
- `replay-ingestion` has `--print-summary` but the handler never branches on it; it always prints JSON after optional file output.
- `replay-ingestion --run-trading` elevates from ingestion replay to trading-cycle execution without an explicit safety confirmation flag.
- `soak-loop paper` clearly warns about Alpaca paper API calls, but lacks explicit `--confirm-paper-api` or equivalent guard.
- Soak-loop commands are broad product workflows and do not emit structured artifact bundles; they print correlation IDs but do not consistently write JSON summaries.
- Some console output in `runtime_soak_loop.py` appears mojibake encoded (`âš`, `â†`, `âœ`), which will look broken in terminals and audit logs.

## 6. Recommended Refactor / Extension

- Keep `run-cycle`, `trigger-job`, `replay`, `replay-debug`, and read-only run inspection under `runtime`.
- Add `list-jobs`, `plan-job`, `plan-cycle`, `list-job-runs`, and `inspect-job-run` before adding more mutating runtime commands.
- Add `--dry-run` to `run-cycle`, `trigger-job`, `replay-ingestion`, and orphan recovery.
- Wire `run-cycle --timestamp` through to `run_trading_cycle(parse_datetime(args.timestamp))`.
- Add explicit safety gates for broker-facing or trading-dispatch paths: paper/live environment display, `--confirm-paper-api` for paper soak, and a stronger confirmation for live-capable commands.
- Move or wrap `soak-loop backtest`, `soak-loop research`, and broad golden-path replay flows under `platform`; keep runtime-only internals reusable.
- Duplicate read-only runtime job/run inspection in `operations` if operators need it alongside health and soak verification.
- Add JSON/artifact output consistently to long-running commands, especially soak loops and manual triggers.
- Add audit logging for manual trigger attempts, including skipped/failed dispatcher resolution and CLI actor/source.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp runtime` | Help parent | yes | Low | Keep. |
| `atp runtime run-cycle` | Functional but unsafe for casual use | yes | High | Honor `--timestamp`, add `--dry-run` and safety gate. |
| `atp runtime trigger-job` | Partially functional | yes | Medium | Add dry-run, complete dispatcher coverage, audit attempts. |
| `atp runtime inspect-manifest` | Read-only useful | partial | Low | Keep here; also expose in diagnostics/admin; parse UUID explicitly. |
| `atp runtime inspect-audit` | Read-only useful | partial | Low | Keep here; also expose broader audit browsing elsewhere. |
| `atp runtime soak-loop backtest` | Works as historical workflow | no | Medium | Move/wrap under `platform` or `research`; add JSON artifacts. |
| `atp runtime soak-loop paper` | Broker-facing soak loop | partial | High | Move/wrap under `operations`/`platform`; add explicit confirmation. |
| `atp runtime soak-loop research` | Historical golden-path workflow | no | Medium | Move/wrap under `platform` or `research`; add artifact output. |
| `atp runtime replay` | Runtime replay evidence command | yes | Medium | Keep; make mutation/reset behavior clearer. |
| `atp runtime replay-debug` | Local wiring validation | yes | Medium | Keep; add preflight/plan mode. |
| `atp runtime replay-ingestion` | Cross-domain replay orchestration | partial | Medium | Keep runtime internals; add ingestion/platform wrapper and dry-run. |
