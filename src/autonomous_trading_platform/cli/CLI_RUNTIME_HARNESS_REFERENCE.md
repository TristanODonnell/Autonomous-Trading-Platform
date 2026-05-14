# CLI Runtime Harness Reference

**Project:** Autonomous Trading Platform
**Scope:** Full audit of all 56 CLI commands across 12 domains
**Audience:** Platform operators and developers
**Purpose:** Operator handbook — what to run, when, and why

---

## Table of Contents

1. [CLI Architecture Overview](#cli-architecture-overview)
2. [Command Discovery Summary](#command-discovery-summary)
3. [Core Daily Debugging Commands (P0)](#core-daily-debugging-commands-p0)
4. [Main Validation Harnesses (P1)](#main-validation-harnesses-p1)
5. [Runtime and Scheduler Operations](#runtime-and-scheduler-operations)
6. [Replay / Soak / Historical Simulation Harnesses](#replay--soak--historical-simulation-harnesses)
7. [Settings / Controls / Governance Verification](#settings--controls--governance-verification)
8. [Research / Experiment / Backfill Commands](#research--experiment--backfill-commands)
9. [External Paper Trading / Broker Commands](#external-paper-trading--broker-commands)
10. [Inspection / Admin Commands](#inspection--admin-commands)
11. [Rare / Specialized Commands](#rare--specialized-commands)
12. [Legacy / Deprecated / Cleanup Candidates](#legacy--deprecated--cleanup-candidates)
13. [Runtime vs Research Separation Recommendations](#runtime-vs-research-separation-recommendations)
14. [Namespace Refactor Recommendations](#namespace-refactor-recommendations)
15. [Recommended Canonical Runtime Paths](#recommended-canonical-runtime-paths)
16. [Suggested Daily Development Workflow](#suggested-daily-development-workflow)
17. [Which Command Should I Use? Decision Table](#which-command-should-i-use-decision-table)
18. [Commands Safe For Local / CI / Replay / Paper](#commands-safe-for-local--ci--replay--paper)
19. [Recommended Future CLI Structure](#recommended-future-cli-structure)

---

## CLI Architecture Overview

### Entry Point

```
src/autonomous_trading_platform/cli/main.py
```

`build_parser()` constructs a root argparse parser and delegates subcommand registration to 12 domain modules via `register(subparsers)`. Each domain module lives under:

```
src/autonomous_trading_platform/cli/commands/<domain>.py
```

The `run_handler()` function dispatches to the handler set by `set_defaults(func=...)` on each subparser and formats errors uniformly.

### Parser Registration Hierarchy

```
main.py build_parser()
├── safety        → safety.register(subparsers)
├── diagnostics   → diagnostics.register(subparsers)
├── ingestion     → ingestion.register(subparsers)
├── strategy      → strategy.register(subparsers)
├── execution     → execution.register(subparsers)
├── runtime       → runtime.register(subparsers)
│   └── soak-loop → register_soak_loop_commands() (runtime_soak_loop.py)
├── backtesting   → backtesting.register(subparsers)
├── admin         → admin.register(subparsers)
├── operations    → operations.register(subparsers)
├── universe      → universe.register(subparsers)
├── features      → features.register(subparsers)
└── research      → research.register(subparsers)
```

`runtime soak-loop` is the only domain with nested subcommand delegation (via a separate `runtime_soak_loop.py` helper module).

### Orchestration Layer

CLI handlers do not contain business logic. They parse arguments and call one of:

| Orchestrator | Domain Role |
|---|---|
| `run_trading_cycle()` | Full live/paper trading cycle |
| `run_trading_evaluation_cycle()` | Strategy signal evaluation only |
| `run_market_ingestion_cycle()` | Market bar ingestion |
| `run_market_backfill_cycle()` | Historical bar backfill |
| `run_corporate_action_ingestion_cycle()` | Corporate actions |
| `run_feature_pipeline_cycle()` | Feature computation |
| `run_universe_selection_cycle()` | Universe selection |
| `run_strategy_auto_promotion_cycle()` | Auto-promotion governance |
| `run_strategy_auto_demotion_cycle()` | Auto-demotion governance |
| `run_strategy_allocation_rebalance_cycle()` | Allocation rebalance |
| `HistoricalIngestionReplayOrchestrator` | Tick-by-tick historical replay |
| `HistoricalResearchGoldenPathOrchestrator` | Research soak loop |
| `PaperTradingGoldenPathOrchestrator` | Paper trading soak (real Alpaca) |
| `RuntimeReplayDebugRunner` | Deterministic local replay (no broker) |
| `ExperimentOrchestrationService` | Experiment orchestration |
| `SimulationRunner` | Direct strategy simulation |
| `ManualTriggerService` | Manual scheduler job trigger with no-overlap lock |

---

## Command Discovery Summary

**Total commands: 56 across 12 domains**

| Domain | Commands | Mutating | Read-Only | Stubs |
|---|---|---|---|---|
| safety | 5 | 4 | 1 | 0 |
| diagnostics | 1 | 0 | 1 | 0 |
| ingestion | 4 | 3 | 1 | 0 |
| strategy | 2 | 1 | 1 | 0 |
| execution | 5 | 2 | 3 | 0 |
| runtime | 6 + 3 soak = 9 | 5 | 4 | 0 |
| backtesting | 10 | 3 | 5 | 2 |
| admin | 3 | 0 | 3 | 0 |
| operations | 1 | 0 | 1 | 0 |
| universe | 7 | 2 | 5 | 0 |
| features | 1 | 1 | 0 | 0 |
| research | 3 | 2 | 1 | 0 |
| **TOTAL** | **56** | **23** | **26** | **2** |

---

## Core Daily Debugging Commands (P0)

These are the commands you will use most frequently during day-to-day development. They are deterministic, non-destructive, and safe to run locally without external API access.

---

### `runtime replay-debug`

| Field | Value |
|---|---|
| **Full command path** | `runtime replay-debug` |
| **Domain** | runtime |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Deterministic local replay of the full runtime trading stack over a historical window. Does NOT submit broker orders. Ingests historical bars, runs strategy evaluation, applies governance/allocation/risk logic, and outputs a structured summary. This is the canonical debugging harness for the trading stack.

**Systems touched:**
- Reads historical market bars from DB
- Runs `RuntimeReplayDebugRunner` (no broker orders submitted)
- No Alpaca API calls
- No persistent state written

**Properties:**
- Mutates DB/runtime state: **NO**
- Uses scheduler cycles: **NO** (standalone runner)
- Uses RuntimeJobRunner/observability: **NO**
- Calls ingestion/backfill: **NO** (reads existing bars)
- Calls external APIs: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp runtime replay-debug \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-02T14:30:00Z \
  --end 2024-01-31T21:00:00Z

# With JSON output
atp runtime replay-debug \
  --symbols AAPL MSFT \
  --start 2024-01-02T14:30:00Z \
  --end 2024-01-10T21:00:00Z \
  --json
```

**Recommended usage frequency:** Daily — this is your primary debugging harness.

**Risks/warnings:** Requires historical bars to exist in the DB for the requested window. Run `ingestion run-backfill` first if the window is not yet populated.

---

### `diagnostics snapshot`

| Field | Value |
|---|---|
| **Full command path** | `diagnostics snapshot` |
| **Domain** | diagnostics |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Captures and prints the current full runtime state: portfolio, allocations, strategy controls, experiments, and activity logs. Use this whenever you need an instant health check of what the system believes to be true right now.

**Systems touched:**
- `RuntimeSnapshotService.capture()`
- Read-only across all major runtime state tables

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp diagnostics snapshot
atp diagnostics snapshot --json
```

**Recommended usage frequency:** Daily / on-demand — run before/after any state-mutating command to verify the effect.

---

### `backtesting read-controls`

| Field | Value |
|---|---|
| **Full command path** | `backtesting read-controls` |
| **Domain** | backtesting (misplaced — see refactor notes) |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `controls inspect` or `admin inspect-controls` |

**Purpose:** Reads the current strategy governance, control state, and allocation overrides from the DB, grouped by frontend section. Use this to confirm that a `seed-controls` or `seed-fixture` write actually landed correctly, or to inspect current control state at any time.

**Systems touched:** Read-only across governance, control state, and allocation repos.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting read-controls
```

**Recommended usage frequency:** Daily — always run after seeding controls to verify.

**Refactor notes:** This command is in the wrong domain. It has nothing to do with backtesting. It is an admin/inspection command and should move to `controls inspect-state` or `admin inspect-controls`.

---

### `backtesting read-settings`

| Field | Value |
|---|---|
| **Full command path** | `backtesting read-settings` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `controls inspect-settings` or `admin inspect-settings` |

**Purpose:** Reads current operator settings from the DB. Use this to confirm seeded settings are present.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting read-settings
```

**Refactor notes:** Same misplacement as `read-controls`. Belongs under `admin` or a `controls` namespace.

---

### `admin inspect-failed-runs`

| Field | Value |
|---|---|
| **Full command path** | `admin inspect-failed-runs` |
| **Domain** | admin |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Lists the most recent failed run manifests. Use this immediately after a cycle failure to identify what went wrong before reading the specific audit log.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp admin inspect-failed-runs
atp admin inspect-failed-runs --limit 50
```

**Recommended usage frequency:** Run whenever a cycle fails or something feels wrong.

---

### `runtime inspect-audit`

| Field | Value |
|---|---|
| **Full command path** | `runtime inspect-audit` |
| **Domain** | runtime |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Dumps all audit log entries for a specific `run-id`. The primary debugging command after a run failure — use `admin inspect-failed-runs` to find the run-id, then this to see what happened.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp runtime inspect-audit --run-id <uuid>
```

**Recommended usage frequency:** After every failed cycle.

---

### `runtime inspect-manifest`

| Field | Value |
|---|---|
| **Full command path** | `runtime inspect-manifest` |
| **Domain** | runtime |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Shows the run manifest for a specific `run-id` — status, timing, job type. Use alongside `inspect-audit` to get the full picture of a run.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp runtime inspect-manifest --run-id <uuid>
```

---

## Main Validation Harnesses (P1)

These commands validate that the system is wired correctly. They are non-mutating and can be run locally, but require a seeded DB to be meaningful.

---

### `backtesting verify-governance-allocation`

| Field | Value |
|---|---|
| **Full command path** | `backtesting verify-governance-allocation` |
| **Domain** | backtesting (misplaced — see refactor notes) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `runtime verify-governance` or `harness verify-governance` |

**Purpose:** Verifies that governance settings (strategy controls, allocation overrides) are correctly wired into the runtime portfolio engine. Confirms that operator control knobs actually affect portfolio behavior. This is a runtime correctness harness, not a backtesting tool.

**Systems touched:**
- `PortfolioEngine` with current governance repos
- No broker calls, no state mutation

**Properties:**
- Mutates DB/runtime state: **NO**
- Calls external APIs: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting verify-governance-allocation \
  --controls path/to/controls.yaml \
  --settings path/to/settings.yaml \
  --total-capital 100000
```

**Risks/warnings:** Requires `--controls` and `--settings` YAML files. Does not auto-load from DB.

**Refactor notes:** This command should be renamed and moved. Suggested: `runtime verify-governance` or `harness verify-governance-allocation`. Nothing about it is specific to backtesting.

---

### `backtesting verify-risk-parameter-effects`

| Field | Value |
|---|---|
| **Full command path** | `backtesting verify-risk-parameter-effects` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `runtime verify-risk` or `harness verify-risk-parameters` |

**Purpose:** Runs baseline deterministic replay, then re-runs with mutated risk parameters to verify that parameter changes actually affect runtime behavior. This is the primary wiring test for risk parameters — use it whenever modifying risk configuration.

**Systems touched:**
- `RuntimeReplayDebugRunner` (baseline + N mutations)
- No broker calls, no persistent state written

**Properties:**
- Mutates DB/runtime state: **NO**
- Calls external APIs: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting verify-risk-parameter-effects \
  --controls controls.yaml \
  --settings settings.yaml \
  --symbols AAPL MSFT \
  --start 2024-01-02T14:30:00Z \
  --end 2024-01-31T21:00:00Z \
  --parameter max_position_pct \
  --parameter stop_loss_pct
```

**Refactor notes:** Same misplacement as `verify-governance-allocation`. This is a runtime correctness harness. Suggested namespace: `runtime verify-risk-parameters` or `harness verify-risk-parameters`.

---

### `backtesting verify-auto-promotion`

| Field | Value |
|---|---|
| **Full command path** | `backtesting verify-auto-promotion` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `runtime verify-promotion` |

**Purpose:** Verifies that `auto_promote_enabled` gates the promotion rules correctly. Use after any change to promotion logic or settings.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting verify-auto-promotion --settings settings.yaml
```

**Refactor notes:** Belongs under `runtime verify-promotion` or `harness verify-auto-promotion`.

---

### `backtesting verify-auto-demotion`

| Field | Value |
|---|---|
| **Full command path** | `backtesting verify-auto-demotion` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `runtime verify-demotion` |

**Purpose:** Verifies that drawdown breaches correctly trigger governance/control/allocation changes. Use after any change to demotion logic.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting verify-auto-demotion --settings settings.yaml
```

---

### `backtesting verify-notification-events`

| Field | Value |
|---|---|
| **Full command path** | `backtesting verify-notification-events` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `runtime verify-notifications` |

**Purpose:** Verifies that `notify_*` flags in settings correctly gate notification events. Use after any change to notification configuration.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting verify-notification-events \
  --controls controls.yaml \
  --settings settings.yaml
```

---

### `operations verify-runtime-soak`

| Field | Value |
|---|---|
| **Full command path** | `operations verify-runtime-soak` |
| **Domain** | operations |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP (or MOVE → `runtime verify-soak`) |

**Purpose:** Post-soak verification. After running a soak loop, use this to validate that the soak window produced acceptable results — checks for stale runs, error rates, and health metrics within the window.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp operations verify-runtime-soak \
  --window-start 2024-01-10T09:00:00Z \
  --window-end 2024-01-10T17:00:00Z \
  --stale-after-minutes 20
```

**Recommended usage:** Always run after a soak loop session to confirm health.

---

### `strategy inspect-readiness`

| Field | Value |
|---|---|
| **Full command path** | `strategy inspect-readiness` |
| **Domain** | strategy |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP (consider RENAME → `ingestion inspect-readiness`) |

**Purpose:** Checks whether ingestion data is sufficiently complete for strategy evaluation at a given timestamp. Use before triggering manual evaluation or debugging data gaps.

**Properties:**
- Mutates DB/runtime state: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp strategy inspect-readiness
atp strategy inspect-readiness --timestamp 2024-01-15T15:00:00Z
```

**Refactor notes:** The readiness check is fundamentally about ingestion completeness, not strategy. Consider moving to `ingestion inspect-readiness`.

---

## Runtime and Scheduler Operations

---

### `runtime run-cycle`

| Field | Value |
|---|---|
| **Full command path** | `runtime run-cycle` |
| **Domain** | runtime |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Manually executes one full trading cycle — the same cycle the scheduler fires automatically. Creates a run manifest and audit log. Use for one-off manual cycle execution outside the scheduler, or for debugging cycle flow in a controlled environment.

**Systems touched:**
- Full trading cycle orchestrator
- Creates `run_manifests` and `audit_logs` entries
- May submit orders depending on environment/safety gate state

**Properties:**
- Mutates DB/runtime state: **YES**
- Uses scheduler cycles: **YES** (directly calls the cycle)
- Uses RuntimeJobRunner/observability: **YES** (creates manifest)
- Calls external APIs: **DEPENDS** (paper/live if environment is armed)
- Deterministic: **NO** (depends on live data and safety gate)
- Safe for local dev: **CONDITIONAL** (safe if kill switch active or simulation env)
- Safe for CI: **NO**

**Example usage:**
```bash
atp runtime run-cycle
```

**Risks/warnings:** If the environment is armed for paper or live trading, this will submit real orders. Always confirm kill switch state with `safety gate-status` before running in non-simulation environments.

---

### `runtime trigger-job`

| Field | Value |
|---|---|
| **Full command path** | `runtime trigger-job` |
| **Domain** | runtime |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Manually triggers a specific scheduler job via `ManualTriggerService` with no-overlap locking (prevents concurrent runs of the same job). Supported jobs: `trading_cycle`, `strategy_auto_promotion_cycle`, `strategy_auto_demotion_cycle`, `strategy_allocation_rebalance_cycle`.

**Systems touched:**
- `ManualTriggerService` with no-overlap lock
- Full job orchestration for the selected job type
- Creates `run_manifests` and `audit_logs` entries

**Properties:**
- Mutates DB/runtime state: **YES**
- Uses RuntimeJobRunner/observability: **YES**
- Safe for local dev: **CONDITIONAL** (safe if simulation env or kill switch active)
- Safe for CI: **NO**

**Example usage:**
```bash
atp runtime trigger-job --job-name trading_cycle
atp runtime trigger-job --job-name strategy_auto_promotion_cycle
atp runtime trigger-job --job-name strategy_auto_demotion_cycle
atp runtime trigger-job --job-name strategy_allocation_rebalance_cycle
```

**Risks/warnings:** Same caution as `runtime run-cycle`. Check gate status first.

---

### `strategy evaluate-bar`

| Field | Value |
|---|---|
| **Full command path** | `strategy evaluate-bar` |
| **Domain** | strategy |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Runs strategy signal evaluation for a single bar timestamp. Useful for debugging evaluation logic without running the full trading cycle.

**Systems touched:**
- `run_trading_evaluation_cycle(timestamp)`
- Mutates evaluation state for that timestamp

**Properties:**
- Mutates DB/runtime state: **YES** (evaluation results)
- Calls external APIs: **NO**
- Safe for local dev: **YES** (does not submit orders)
- Safe for CI: **CONDITIONAL**

**Example usage:**
```bash
atp strategy evaluate-bar --timestamp 2024-01-15T15:00:00Z
```

---

## Replay / Soak / Historical Simulation Harnesses

---

### `runtime replay-ingestion`

| Field | Value |
|---|---|
| **Full command path** | `runtime replay-ingestion` |
| **Domain** | runtime |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Replays historical ingestion tick-by-tick using `HistoricalIngestionReplayOrchestrator`. Use this to re-populate ingestion state for a historical window, or to test ingestion pipeline behavior over a controlled time range. Optionally runs trading evaluation (`--run-trading` flag).

**Systems touched:**
- `HistoricalIngestionReplayOrchestrator.run()`
- Writes ingested bar data to DB
- Optionally triggers trading evaluation per bar

**Properties:**
- Mutates DB/runtime state: **YES** (ingests bars)
- Mutates if `--run-trading`: **YES** (evaluation state too)
- Calls external APIs: **NO** (reads historical data from DB or file)
- Deterministic: **YES** (given fixed input data)
- Safe for local dev: **YES**
- Safe for CI: **CONDITIONAL** (only if data is pre-loaded)

**Example usage:**
```bash
atp runtime replay-ingestion \
  --symbols AAPL MSFT \
  --start 2024-01-02T14:30:00Z \
  --end 2024-01-31T21:00:00Z

# With trading evaluation
atp runtime replay-ingestion \
  --symbols AAPL MSFT \
  --start 2024-01-02T14:30:00Z \
  --end 2024-01-31T21:00:00Z \
  --run-trading
```

**Risks/warnings:** When `--run-trading` is set, this writes evaluation state. Understand whether you want to write or read — use `runtime replay-debug` if you want a read-only simulation.

---

### `runtime soak-loop backtest`

| Field | Value |
|---|---|
| **Full command path** | `runtime soak-loop backtest` |
| **Domain** | runtime |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Walks historical bars and writes fills and position/cash snapshots as if trades had been executed. This is the backtesting soak harness — it simulates position tracking over a historical window with real DB persistence.

**Systems touched:**
- `BacktestConfig` + orchestrator
- Writes fills and position/cash snapshots to DB
- No Alpaca API calls

**Properties:**
- Mutates DB/runtime state: **YES** (fills and snapshots)
- Calls external APIs: **NO**
- Deterministic: **YES** (given fixed data)
- Safe for local dev: **YES** (no broker)
- Safe for CI: **CONDITIONAL**

**Example usage:**
```bash
atp runtime soak-loop backtest \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-02 \
  --end 2024-03-31 \
  --initial-capital 100000 \
  --strategy-id <uuid>
```

**Risks/warnings:** Writes fills and snapshots to DB. Run against a development/test database, not production.

---

### `runtime soak-loop research`

| Field | Value |
|---|---|
| **Full command path** | `runtime soak-loop research` |
| **Domain** | runtime |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Historical research soak loop using `HistoricalResearchGoldenPathOrchestrator`. Runs ingestion, feature computation, and trading over a historical window. Designed for extended research runs. Optionally accepts an experiment plan YAML for structured parameter exploration. Supports graceful shutdown via signal handler.

**Systems touched:**
- `HistoricalResearchGoldenPathOrchestrator`
- Writes ingestion, feature, and evaluation state
- Uses `InMemoryNoOverlapLock`
- No Alpaca API calls

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **NO**
- Market-hours aware: **NO**
- Safe for local dev: **YES**
- Safe for CI: **CONDITIONAL**

**Example usage:**
```bash
atp runtime soak-loop research \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-02 \
  --end 2024-06-30

# With experiment plan
atp runtime soak-loop research \
  --symbols AAPL MSFT \
  --start 2024-01-02 \
  --end 2024-03-31 \
  --experiment-plan path/to/plan.yaml \
  --loop
```

---

## Settings / Controls / Governance Verification

---

### `backtesting seed-fixture`

| Field | Value |
|---|---|
| **Full command path** | `backtesting seed-fixture` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `controls seed-fixture` or `admin seed-fixture` |

**Purpose:** Seeds the DB with a complete fixture: strategies, allocation overrides, and optionally operator settings and runtime control state. This is the primary setup command for a development/test environment. Use `--dry-run` to preview without writing.

**Systems touched:**
- Writes to: `strategy_configs`, `strategy_control_state`, `allocation_overrides`, `operator_settings_snapshot`, `runtime_control_state_snapshot`

**Properties:**
- Mutates DB/runtime state: **YES** (unless `--dry-run`)
- Calls external APIs: **NO**
- Deterministic: **YES** (given same YAML)
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
# Dry run first
atp backtesting seed-fixture --fixture fixtures/base.yaml --dry-run

# Apply
atp backtesting seed-fixture --fixture fixtures/base.yaml
```

**Recommended usage frequency:** Once per dev environment setup, or whenever resetting state for a test run.

**Refactor notes:** `seed-fixture` is not a backtesting concept. It is environment configuration. Move to `controls seed-fixture` or `admin seed`.

---

### `backtesting seed-controls`

| Field | Value |
|---|---|
| **Full command path** | `backtesting seed-controls` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `controls seed` |

**Purpose:** Seeds strategy governance, control state, and allocation overrides from a YAML config. The `--clean` flag wipes existing rows before writing, allowing a full reset.

**Systems touched:**
- Writes to: `strategy_governance`, `strategy_control_state`, `allocation_overrides`

**Properties:**
- Mutates DB/runtime state: **YES**
- `--clean` is destructive (deletes existing rows before inserting)
- Safe for local dev: **YES** (on dev DB)
- Safe for CI: **YES** (on test DB)

**Example usage:**
```bash
# Full reset
atp backtesting seed-controls --config controls.yaml --clean

# Additive (no wipe)
atp backtesting seed-controls --config controls.yaml
```

**Risks/warnings:** `--clean` deletes existing control rows. Do not run against a production database.

---

### `backtesting seed-settings`

| Field | Value |
|---|---|
| **Full command path** | `backtesting seed-settings` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `controls seed-settings` or `admin seed-settings` |

**Purpose:** Seeds operator settings from a YAML config file.

**Systems touched:** Writes to `operator_settings`.

**Properties:**
- Mutates DB/runtime state: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp backtesting seed-settings --config settings.yaml
```

---

### `backtesting read-portfolio`

| Field | Value |
|---|---|
| **Full command path** | `backtesting read-portfolio` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `diagnostics read-portfolio` or `admin inspect-portfolio` |

**Purpose:** Reads the current portfolio state exactly as the API serves it to the frontend. Use to verify that portfolio data is correctly assembled after a soak run or backtest.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp backtesting read-portfolio
```

**Refactor notes:** This is a portfolio inspection command. It belongs under `diagnostics` or `admin`, not `backtesting`.

---

### `backtesting read-dashboard`

| Field | Value |
|---|---|
| **Full command path** | `backtesting read-dashboard` |
| **Domain** | backtesting (misplaced) |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | MOVE → `diagnostics read-dashboard` or `admin inspect-dashboard` |

**Purpose:** Reads the current dashboard state exactly as the API serves it to the frontend. Use to verify dashboard data after a run.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp backtesting read-dashboard
```

---

## Research / Experiment / Backfill Commands

---

### `research run-experiment`

| Field | Value |
|---|---|
| **Full command path** | `research run-experiment` |
| **Domain** | research |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Full research pipeline — strategy generation, experiment orchestration, and simulation. Always persists results to DB via `ExperimentOrchestrationService`. Accepts a YAML config file or inline arguments. Supports experiment types: `ab`, `sweep`, `time_segmentation`, `rolling_window`, `cross_universe`.

**Systems touched:**
- `ExperimentOrchestrationService.run_experiment()` or `run_staged_experiment()`
- Writes experiment results to DB

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **NO**
- Deterministic: **YES** (given fixed `--random-seed`)
- Safe for local dev: **YES** (research DB)
- Safe for CI: **CONDITIONAL**

**Example usage:**
```bash
# From config file
atp research run-experiment --config experiments/sweep_v1.yaml

# Inline
atp research run-experiment \
  --experiment-id exp-001 \
  --dataset-version-id ds-001 \
  --price-basis close \
  --symbols AAPL MSFT \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --strategy-type momentum \
  --random-seed 42 \
  --experiment-type sweep \
  --parameter-space '{"lookback": [5, 10, 20], "threshold": [0.01, 0.02]}'
```

---

### `research run-simulation`

| Field | Value |
|---|---|
| **Full command path** | `research run-simulation` |
| **Domain** | research |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Ad-hoc single-strategy simulation. When `--experiment-id` is provided, persists to DB via `ExperimentOrchestrationService`. Without it, uses `SimulationRunner` directly with no DB writes. Use the no-`--experiment-id` path for quick exploratory runs.

**Properties:**
- Mutates DB/runtime state: **CONDITIONAL** (only with `--experiment-id`)
- Calls external APIs: **NO**
- Deterministic: **YES** (given fixed `--random-seed`)
- Safe for local dev: **YES**
- Safe for CI: **YES** (without `--experiment-id`)

**Example usage:**
```bash
# Quick exploratory (no DB writes)
atp research run-simulation \
  --dataset-version-id ds-001 \
  --price-basis close \
  --symbols AAPL MSFT \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --strategy-type momentum \
  --random-seed 42 \
  --strategy-id strat-001 \
  --strategy-parameters '{"lookback": 10}'

# With DB persistence
atp research run-simulation \
  --experiment-id exp-001 \
  ... (same flags above)
```

---

### `research generate-strategies`

| Field | Value |
|---|---|
| **Full command path** | `research generate-strategies` |
| **Domain** | research |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Dry-run strategy generation — shows what strategies would be generated for a given parameter space without running any simulation or writing to the DB. Use to preview a sweep or validate a parameter space before committing to a full experiment.

**Properties:**
- Mutates DB/runtime state: **NO**
- Calls external APIs: **NO**
- Deterministic: **YES**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
# Grid search
atp research generate-strategies \
  --strategy-type momentum \
  --parameter-space '{"lookback": [5, 10, 20], "threshold": [0.01, 0.02]}' \
  --show-configs

# Random sampling
atp research generate-strategies \
  --strategy-type momentum \
  --parameter-space '{"lookback": [5, 20], "threshold": [0.01, 0.05]}' \
  --generator random \
  --n-samples 25 \
  --random-seed 42
```

---

### `ingestion run-backfill`

| Field | Value |
|---|---|
| **Full command path** | `ingestion run-backfill` |
| **Domain** | ingestion |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Runs a historical bar backfill via `run_market_backfill_cycle()`. Use this to pre-populate bar data before running `runtime replay-debug` or any replay harness over a new window.

**Systems touched:**
- Calls `run_market_backfill_cycle(symbols, start, end)`
- Likely calls external market data API

**Properties:**
- Mutates DB/runtime state: **YES** (writes historical bars)
- Calls external APIs: **YES** (market data provider)
- Deterministic: **YES** (idempotent for the same window/symbols)
- Safe for local dev: **YES**
- Safe for CI: **NO** (external API call)

**Example usage:**
```bash
atp ingestion run-backfill \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-01T00:00:00Z \
  --end 2024-06-30T23:59:59Z
```

**Recommended usage:** Run once to seed a new historical window before any replay debugging. Not needed again for the same window if data already exists.

---

### `features run-pipeline`

| Field | Value |
|---|---|
| **Full command path** | `features run-pipeline` |
| **Domain** | features |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Runs the feature computation pipeline for a dataset version. Supports selective feature inclusion via flags. Sets up telemetry for the run.

**Systems touched:**
- `run_feature_pipeline_cycle()` with `setup_telemetry("cli-feature-pipeline")`
- Writes computed features to DB

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp features run-pipeline \
  --dataset-version-id ds-001 \
  --symbols AAPL MSFT \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --include-returns \
  --include-volatility \
  --include-moving-average
```

---

## External Paper Trading / Broker Commands

These commands interact with external systems (Alpaca) or mutate live/paper runtime state. Treat with care.

---

### `runtime soak-loop paper`

| Field | Value |
|---|---|
| **Full command path** | `runtime soak-loop paper` |
| **Domain** | runtime |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Paper trading soak loop using `PaperTradingGoldenPathOrchestrator`. Makes real Alpaca API calls against the paper trading account. Market-hours aware (uses `RealMarketCalendar` and `RealTradingClock`). Runs indefinitely with graceful shutdown via signal handler. Supports `fast`, `realistic`, and `single` modes.

**Systems touched:**
- `PaperTradingGoldenPathOrchestrator`
- Real Alpaca paper trading API (not simulated)
- Writes runtime state to DB
- Market calendar / trading clock aware

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **YES (Alpaca paper)**
- Market-hours aware: **YES**
- Deterministic: **NO**
- Safe for local dev: **CONDITIONAL** (safe vs. paper account only)
- Safe for CI: **NO**

**Example usage:**
```bash
# Fast mode (compressed timing)
atp runtime soak-loop paper --mode fast

# Realistic mode (real market timing)
atp runtime soak-loop paper --mode realistic

# Single cycle
atp runtime soak-loop paper --mode single
```

**Risks/warnings:**
- This submits real orders to your Alpaca paper account.
- Requires market hours (or fast mode) to produce meaningful results.
- Do not run against a live account.
- Always verify with `safety gate-status` before starting.
- Use `operations verify-runtime-soak` after completion to validate health.

---

### `execution reconcile-order`

| Field | Value |
|---|---|
| **Full command path** | `execution reconcile-order` |
| **Domain** | execution |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Reconciles a single tracked order with the broker. Fetches broker order state, applies fills, updates position/cash snapshots, and computes risk snapshots. Use to manually reconcile a specific order that failed automated reconciliation.

**Systems touched:**
- `OrderReconciliationService.reconcile_order()`
- `PostFillAccountingService.apply_fill()`
- `RiskSnapshotService.compute_snapshot()`
- Writes fills, position, cash, and risk snapshots

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **YES (Alpaca broker)**
- Safe for local dev: **CONDITIONAL**
- Safe for CI: **NO**

**Example usage:**
```bash
atp execution reconcile-order --order-id <uuid>
```

**Risks/warnings:** Makes a live broker API call. Use only for specific reconciliation needs, not bulk operations.

---

### `execution reconcile-open-orders`

| Field | Value |
|---|---|
| **Full command path** | `execution reconcile-open-orders` |
| **Domain** | execution |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Reconciles all open orders against the broker. Equivalent to triggering the reconciliation job manually.

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **YES (Alpaca broker)**
- Safe for CI: **NO**

**Example usage:**
```bash
atp execution reconcile-open-orders
```

**Risks/warnings:** Bulk broker API calls. Use carefully.

---

### `safety arm-live`

| Field | Value |
|---|---|
| **Full command path** | `safety arm-live` |
| **Domain** | safety |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Arms the live trading gate. Requires `--reason` and `--armed-by`. This is an explicit opt-in to live trading — do not run unless intentionally enabling live execution.

**Properties:**
- Mutates DB/runtime state: **YES (arms live gate)**
- Calls external APIs: **NO** (gate state only)
- Deterministic: **YES**
- Safe for local dev: **DANGEROUS — arms live trading**
- Safe for CI: **NO**

**Example usage:**
```bash
atp safety arm-live \
  --reason "Enabling live trading for scheduled session" \
  --armed-by "operator-name"
```

**Risks/warnings:** **This enables real money trading.** Never run without explicit intent. Always pair with `safety gate-status` to verify state after.

---

### `safety disarm-live`

| Field | Value |
|---|---|
| **Full command path** | `safety disarm-live` |
| **Domain** | safety |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Disarms the live trading gate. Run this first in any emergency or before any maintenance window.

**Properties:**
- Mutates DB/runtime state: **YES**
- Safe for local dev: **YES** (safe to run, always appropriate to disarm)

**Example usage:**
```bash
atp safety disarm-live
```

---

### `safety enable-kill-switch`

| Field | Value |
|---|---|
| **Full command path** | `safety enable-kill-switch` |
| **Domain** | safety |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Enables the global kill switch. Requires `--reason` and `--updated-by`. This halts all trading activity across all environments.

**Properties:**
- Mutates DB/runtime state: **YES**

**Example usage:**
```bash
atp safety enable-kill-switch \
  --reason "Emergency halt — unexpected drawdown" \
  --updated-by "operator-name"
```

**Risks/warnings:** Immediately halts all trading. Use in emergencies or before maintenance.

---

### `safety disable-kill-switch`

| Field | Value |
|---|---|
| **Full command path** | `safety disable-kill-switch` |
| **Domain** | safety |
| **Priority** | P1 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **YES**

**Example usage:**
```bash
atp safety disable-kill-switch \
  --reason "Resuming after maintenance" \
  --updated-by "operator-name"
```

---

### `safety gate-status`

| Field | Value |
|---|---|
| **Full command path** | `safety gate-status` |
| **Domain** | safety |
| **Priority** | P0 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Read-only query of the current live trading gate status. Run before any state-mutating operation to understand current safety state.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp safety gate-status --account-id <account-id>
```

**Recommended usage frequency:** Always before arming/disarming or running live-adjacent commands.

---

## Inspection / Admin Commands

---

### `execution inspect-order`

| Field | Value |
|---|---|
| **Full command path** | `execution inspect-order` |
| **Domain** | execution |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp execution inspect-order --order-id <uuid>
```

---

### `execution inspect-position`

| Field | Value |
|---|---|
| **Full command path** | `execution inspect-position` |
| **Domain** | execution |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp execution inspect-position --symbol AAPL
```

---

### `execution inspect-cash`

| Field | Value |
|---|---|
| **Full command path** | `execution inspect-cash` |
| **Domain** | execution |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp execution inspect-cash
```

---

### `ingestion inspect-bar`

| Field | Value |
|---|---|
| **Full command path** | `ingestion inspect-bar` |
| **Domain** | ingestion |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Queries a specific stored market bar by symbol and timestamp. Use to verify that backfill or ingestion wrote a specific bar correctly.

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp ingestion inspect-bar --symbol AAPL --timestamp 2024-01-15T15:00:00Z
```

---

### `admin inspect-config`

| Field | Value |
|---|---|
| **Full command path** | `admin inspect-config` |
| **Domain** | admin |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Shows current application configuration with secrets redacted (database URL, broker API keys/secrets shown as present/absent, not values).

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**
- Safe for CI: **YES**

**Example usage:**
```bash
atp admin inspect-config
```

---

### `admin inspect-env`

| Field | Value |
|---|---|
| **Full command path** | `admin inspect-env` |
| **Domain** | admin |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Shows current environment variable state (APP_ENV, LOG_LEVEL, database/API key presence).

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp admin inspect-env
```

---

### `universe inspect-active`

| Field | Value |
|---|---|
| **Full command path** | `universe inspect-active` |
| **Domain** | universe |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp universe inspect-active
atp universe inspect-active --timestamp 2024-01-15T00:00:00Z
```

---

### `universe inspect-symbols`

| Field | Value |
|---|---|
| **Full command path** | `universe inspect-symbols` |
| **Domain** | universe |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp universe inspect-symbols --timestamp 2024-01-15T00:00:00Z
```

---

### `universe inspect-symbol`

| Field | Value |
|---|---|
| **Full command path** | `universe inspect-symbol` |
| **Domain** | universe |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Example usage:**
```bash
atp universe inspect-symbol --symbol AAPL --timestamp 2024-01-15T00:00:00Z
```

---

### `universe inspect-ingestion-input`

| Field | Value |
|---|---|
| **Full command path** | `universe inspect-ingestion-input` |
| **Domain** | universe |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Example usage:**
```bash
atp universe inspect-ingestion-input --timestamp 2024-01-15T00:00:00Z
```

---

### `universe validate-active`

| Field | Value |
|---|---|
| **Full command path** | `universe validate-active` |
| **Domain** | universe |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp universe validate-active --timestamp 2024-01-15T00:00:00Z
```

---

## Rare / Specialized Commands

---

### `ingestion run-bars`

| Field | Value |
|---|---|
| **Full command path** | `ingestion run-bars` |
| **Domain** | ingestion |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Runs the live market bar ingestion cycle. Typically triggered by the scheduler during market hours; manual invocation is rare but useful for testing ingestion in isolation.

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **YES (market data)**
- Safe for CI: **NO**

**Example usage:**
```bash
atp ingestion run-bars
atp ingestion run-bars --timestamp 2024-01-15T15:00:00Z
```

---

### `ingestion run-corporate-actions`

| Field | Value |
|---|---|
| **Full command path** | `ingestion run-corporate-actions` |
| **Domain** | ingestion |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **YES**
- Safe for CI: **NO**

**Example usage:**
```bash
atp ingestion run-corporate-actions
```

---

### `universe select-now`

| Field | Value |
|---|---|
| **Full command path** | `universe select-now` |
| **Domain** | universe |
| **Priority** | P3 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Properties:**
- Mutates DB/runtime state: **YES**
- Safe for local dev: **YES** (idempotent)

**Example usage:**
```bash
atp universe select-now
atp universe select-now --timestamp 2024-01-15T00:00:00Z
```

---

### `universe seed`

| Field | Value |
|---|---|
| **Full command path** | `universe seed` |
| **Domain** | universe |
| **Priority** | P2 |
| **Freshness** | CURRENT |
| **Refactor action** | KEEP |

**Purpose:** Creates a universe snapshot from an explicit symbol list. Closes any open snapshots. Use this to manually configure the universe for a test/dev environment without running the full selection cycle.

**Properties:**
- Mutates DB/runtime state: **YES**
- Calls external APIs: **NO**
- Safe for local dev: **YES**

**Example usage:**
```bash
atp universe seed \
  --symbols AAPL MSFT TSLA GOOGL \
  --timestamp 2024-01-01T00:00:00Z \
  --source manual
```

---

## Legacy / Deprecated / Cleanup Candidates

---

### `backtesting run`

| Field | Value |
|---|---|
| **Full command path** | `backtesting run` |
| **Domain** | backtesting |
| **Priority** | P4 |
| **Freshness** | STALE |
| **Refactor action** | DELETE_CANDIDATE |

**Purpose:** Placeholder stub. Handler calls `not_implemented()`. Does nothing.

**Replacement:** `runtime soak-loop backtest` for DB-persisted backtest simulation; `runtime replay-debug` for deterministic local replay.

**Cleanup notes:** Delete this command. It creates confusion about whether there is a functional `backtesting run` command. The `runtime soak-loop backtest` and `runtime replay-debug` commands serve the actual need.

---

### `backtesting inspect-results`

| Field | Value |
|---|---|
| **Full command path** | `backtesting inspect-results` |
| **Domain** | backtesting |
| **Priority** | P4 |
| **Freshness** | STALE |
| **Refactor action** | DELETE_CANDIDATE |

**Purpose:** Placeholder stub. Handler calls `not_implemented()`. Does nothing.

**Replacement:** `diagnostics snapshot`, `backtesting read-portfolio`, `runtime inspect-manifest`.

**Cleanup notes:** Delete or implement. Currently misleads users into thinking there is a backtest results inspection tool.

---

### All `backtesting verify-*` and `backtesting seed-*` and `backtesting read-*`

| Field | Value |
|---|---|
| **Freshness** | NEEDS_REVIEW |
| **Refactor action** | MOVE |

These 8 commands are functional and important, but are incorrectly namespaced. The `backtesting` domain has become a catch-all for governance validation, control seeding, state inspection, and runtime verification. None of these are backtesting operations in the traditional sense.

See the [Namespace Refactor Recommendations](#namespace-refactor-recommendations) section for the proposed reorganization.

---

## Runtime vs Research Separation Recommendations

The current CLI conflates three distinct types of work that should be clearly separated in both naming and tooling:

### 1. Runtime Validation (deterministic, local, no broker)

Commands that verify the runtime trading stack behaves correctly given a known input. These are unit-test-like harnesses for the runtime stack.

**Current home:** Scattered across `backtesting verify-*` and `runtime replay-debug`
**Recommended home:** `runtime verify-*` or a dedicated `harness` namespace

| Current Command | Recommended New Path |
|---|---|
| `backtesting verify-governance-allocation` | `runtime verify-governance` |
| `backtesting verify-risk-parameter-effects` | `runtime verify-risk-parameters` |
| `backtesting verify-auto-promotion` | `runtime verify-promotion` |
| `backtesting verify-auto-demotion` | `runtime verify-demotion` |
| `backtesting verify-notification-events` | `runtime verify-notifications` |
| `runtime replay-debug` | KEEP — this is already correctly named and placed |

### 2. State Setup / Environment Configuration

Commands that seed or inspect DB state for dev/test environments. These are developer tooling, not backtesting.

**Current home:** `backtesting seed-*`, `backtesting read-*`
**Recommended home:** `controls` namespace or `admin`

| Current Command | Recommended New Path |
|---|---|
| `backtesting seed-fixture` | `controls seed-fixture` |
| `backtesting seed-controls` | `controls seed` |
| `backtesting seed-settings` | `controls seed-settings` |
| `backtesting read-controls` | `controls inspect` |
| `backtesting read-settings` | `controls inspect-settings` |
| `backtesting read-portfolio` | `diagnostics portfolio` |
| `backtesting read-dashboard` | `diagnostics dashboard` |

### 3. Research / Experiment Flows

Historical simulation for strategy research. These correctly live under `research` and `runtime soak-loop research`. No changes recommended.

---

## Namespace Refactor Recommendations

### Current State: `backtesting` Domain (10 commands)

The `backtesting` domain has become a junk drawer. It contains:
- 2 stub commands (delete)
- 5 runtime validation harnesses (move to `runtime verify-*`)
- 3 state setup / inspection commands (move to `controls` or `admin`)

### Proposed New `controls` Domain

Create a new `controls` namespace for all environment configuration and state inspection:

```
controls seed-fixture    ← was: backtesting seed-fixture
controls seed            ← was: backtesting seed-controls
controls seed-settings   ← was: backtesting seed-settings
controls inspect         ← was: backtesting read-controls
controls inspect-settings ← was: backtesting read-settings
```

### Proposed Expansion of `runtime verify-*`

Add verification subcommands to the `runtime` domain:

```
runtime verify-governance       ← was: backtesting verify-governance-allocation
runtime verify-risk-parameters  ← was: backtesting verify-risk-parameter-effects
runtime verify-promotion        ← was: backtesting verify-auto-promotion
runtime verify-demotion         ← was: backtesting verify-auto-demotion
runtime verify-notifications    ← was: backtesting verify-notification-events
```

### Proposed Expansion of `diagnostics`

```
diagnostics portfolio   ← was: backtesting read-portfolio
diagnostics dashboard   ← was: backtesting read-dashboard
```

### Rename `strategy inspect-readiness`

The readiness check is about ingestion data completeness, not strategy logic:

```
ingestion inspect-readiness   ← was: strategy inspect-readiness
```

---

## Recommended Canonical Runtime Paths

### 1. Deterministic Local Debug Path (daily development)

**Preferred commands:** `runtime replay-debug` → `runtime inspect-manifest` → `runtime inspect-audit`

**Why preferred:** Fully deterministic, no broker API, no persistent state mutation, safe everywhere. `RuntimeReplayDebugRunner` exercises the full runtime trading stack (ingestion reads, governance, allocation, risk, signal evaluation) without any side effects.

**Prerequisite:** Historical bars must exist. If they do not, run `ingestion run-backfill` first (one-time, external API call).

**Older patterns to avoid:** Directly calling `runtime run-cycle` for debugging (not deterministic, mutates state, may submit orders).

---

### 2. Runtime Verification Path (before/after any governance or risk change)

**Preferred commands:**
```
backtesting verify-governance-allocation   → future: runtime verify-governance
backtesting verify-risk-parameter-effects  → future: runtime verify-risk-parameters
backtesting verify-auto-promotion          → future: runtime verify-promotion
backtesting verify-auto-demotion           → future: runtime verify-demotion
```

**Why preferred:** These are the wiring tests for the control plane. Run them after any change to governance, risk, or promotion/demotion logic to confirm the change actually affects runtime behavior.

**Prerequisite:** Appropriate YAML controls and settings files.

---

### 3. Historical Replay Path (pre-seeding data for replay debug)

**Preferred commands:** `ingestion run-backfill` → (optionally) `runtime replay-ingestion` → `runtime replay-debug`

**Why preferred:** `run-backfill` is the right way to populate a new window. `replay-ingestion` is for tick-by-tick replay with optional trading. `replay-debug` is for non-mutating analysis.

**Older patterns to avoid:** Using `runtime soak-loop backtest` when you just want to read behavior — the soak loop writes fills to the DB, which is more than you need for debugging.

---

### 4. Paper Trading Validation Path

**Preferred commands:** `safety gate-status` → `runtime soak-loop paper --mode single` → `operations verify-runtime-soak`

**Why preferred:** `soak-loop paper` is the only correctly instrumented paper trading harness with real market calendar awareness and Alpaca integration. Always verify safety gate state first, and run `verify-runtime-soak` after to confirm health.

**Older patterns to avoid:** Running `runtime run-cycle` in paper mode ad-hoc — it works but lacks the orchestrated safety checks and post-run verification that the soak loop includes.

---

### 5. Research / Backfill Path

**Preferred commands:** `research run-experiment` (from config YAML) or `runtime soak-loop research`

**Why preferred:** `run-experiment` is the structured path with full DB persistence and experiment tracking. `soak-loop research` is better for open-ended extended runs where you want to monitor behavior rather than capture structured results.

**Older patterns to avoid:** Running raw simulations via `research run-simulation` without `--experiment-id` if you need to compare results later — without the experiment ID, no results are persisted.

---

## Suggested Daily Development Workflow

### Morning Setup (once per session)
```bash
# 1. Check current state
atp diagnostics snapshot

# 2. Verify safety gate
atp safety gate-status --account-id <account-id>

# 3. Check for any failed runs from overnight
atp admin inspect-failed-runs --limit 10
```

### Before Making Any Governance/Risk Change
```bash
# 1. Confirm current controls
atp backtesting read-controls
atp backtesting read-settings

# 2. Run baseline verification
atp backtesting verify-governance-allocation \
  --controls controls.yaml --settings settings.yaml

# 3. Make your change (edit YAML / DB)

# 4. Re-seed
atp backtesting seed-controls --config controls.yaml --clean
atp backtesting seed-settings --config settings.yaml

# 5. Verify again
atp backtesting read-controls
atp backtesting verify-governance-allocation \
  --controls controls.yaml --settings settings.yaml
```

### Debugging a Failing Cycle
```bash
# 1. Find the failed run
atp admin inspect-failed-runs

# 2. Read the manifest
atp runtime inspect-manifest --run-id <uuid>

# 3. Read the audit log
atp runtime inspect-audit --run-id <uuid>

# 4. Reproduce deterministically (read-only, no state mutation)
atp runtime replay-debug \
  --symbols AAPL MSFT \
  --start <window-start> \
  --end <window-end>
```

### Preparing a New Replay Window
```bash
# 1. Backfill data (external API call, run once)
atp ingestion run-backfill \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-01T00:00:00Z \
  --end 2024-06-30T23:59:59Z

# 2. Verify a specific bar arrived
atp ingestion inspect-bar --symbol AAPL --timestamp 2024-01-02T15:00:00Z

# 3. Replay and debug
atp runtime replay-debug \
  --symbols AAPL MSFT TSLA \
  --start 2024-01-02T14:30:00Z \
  --end 2024-06-28T21:00:00Z
```

---

## Which Command Should I Use? Decision Table

| Goal | Command |
|---|---|
| Understand current runtime state | `diagnostics snapshot` |
| Debug a failing cycle | `admin inspect-failed-runs` → `runtime inspect-audit` |
| Replay trading logic without side effects | `runtime replay-debug` |
| Verify governance controls are wired | `backtesting verify-governance-allocation` |
| Verify risk parameters are wired | `backtesting verify-risk-parameter-effects` |
| Verify promotion logic | `backtesting verify-auto-promotion` |
| Verify demotion logic | `backtesting verify-auto-demotion` |
| Seed dev/test environment | `backtesting seed-fixture` |
| Inspect current control state | `backtesting read-controls` |
| Inspect current settings | `backtesting read-settings` |
| Pre-populate historical bars | `ingestion run-backfill` |
| Run paper trading soak | `runtime soak-loop paper` |
| Run research soak | `runtime soak-loop research` |
| Run historical backtest soak | `runtime soak-loop backtest` |
| Manually trigger a scheduler job | `runtime trigger-job` |
| Check safety gate state | `safety gate-status` |
| Emergency halt | `safety enable-kill-switch` |
| Inspect current portfolio | `backtesting read-portfolio` |
| Inspect current dashboard | `backtesting read-dashboard` |
| Run a research experiment | `research run-experiment` |
| Dry-run strategy generation | `research generate-strategies` |
| Inspect specific order | `execution inspect-order` |
| Check ingestion readiness | `strategy inspect-readiness` |
| Verify soak loop health | `operations verify-runtime-soak` |

---

## Commands Safe For Local / CI / Replay / Paper

### Safe for local deterministic debugging (no external calls, no state mutation)

```
runtime replay-debug
diagnostics snapshot
backtesting read-controls
backtesting read-settings
backtesting read-portfolio
backtesting read-dashboard
backtesting verify-governance-allocation
backtesting verify-risk-parameter-effects
backtesting verify-auto-promotion
backtesting verify-auto-demotion
backtesting verify-notification-events
admin inspect-config
admin inspect-env
admin inspect-failed-runs
runtime inspect-manifest
runtime inspect-audit
execution inspect-order
execution inspect-position
execution inspect-cash
ingestion inspect-bar
universe inspect-active
universe inspect-symbols
universe inspect-symbol
universe inspect-ingestion-input
universe validate-active
safety gate-status
strategy inspect-readiness
research generate-strategies
operations verify-runtime-soak
```

### Safe for CI (no external API calls, predictable behavior)

Subset of above — all read-only commands plus:
```
backtesting seed-fixture (on test DB)
backtesting seed-controls (on test DB)
backtesting seed-settings (on test DB)
research run-simulation (without --experiment-id)
universe seed
```

### Safe for offline simulation (require pre-loaded data, no external calls)

```
runtime replay-debug
runtime replay-ingestion
runtime soak-loop backtest
runtime soak-loop research
research run-simulation
research run-experiment
features run-pipeline
```

### Require external API / market hours

```
ingestion run-bars
ingestion run-backfill
ingestion run-corporate-actions
runtime soak-loop paper
execution reconcile-order
execution reconcile-open-orders
```

### NOT safe without explicit intent (mutate live/paper state)

```
safety arm-live
safety disarm-live
safety enable-kill-switch
safety disable-kill-switch
runtime run-cycle          (if not simulation env)
runtime trigger-job        (if not simulation env)
runtime soak-loop paper    (real Alpaca API)
execution reconcile-order  (real broker call)
execution reconcile-open-orders (real broker call)
```

---
