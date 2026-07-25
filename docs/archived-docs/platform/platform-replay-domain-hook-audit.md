# Platform Replay Domain Hook Audit

> Generated: 2026-06-03
> Branch: cli-entrypoint-updates-platform-test-prep
> Scope: All domain CLI commands in `src/autonomous_trading_platform/cli/commands/`

## Summary Table

| Domain | Current Capability | Missing Hook | Needs Timeline Events? | Needs Failure Injection? | Priority |
|---|---|---|---|---|---|
| **admin** | validate-config, doctor, inspect-failed-runs — all read-only preflight | `validate_admin_preflight(session, timestamp, ctx)` | No | No | P1 |
| **diagnostics** | snapshot (--section, --output), RuntimeSnapshotService.capture() returns structured RuntimeSnapshot | `snapshot_diagnostics_at_timestamp(session, timestamp, ctx)` | No | No | P1 |
| **safety** | emergency-halt, release-kill-switch, pre-trade-check (--bar-timestamp), assert-gate, audit-log | `apply_safety_event(session, timestamp, event, ctx)` | Yes | Yes (safety_emergency_halt injection) | P0 |
| **controls** | export, seed (--dry-run), verify-runtime-gates, audit-log | `apply_controls_event(session, timestamp, event, ctx)` | Yes | No | P0 |
| **settings** | snapshot (SHA256 hash), export, seed (--dry-run), verify-runtime-effect | `apply_settings_event(session, timestamp, event, ctx)` | Yes | No | P0 |
| **universe** | select-now (--timestamp, --dry-run), rotate (--timestamp, --dry-run), history-for-date, replay-timeline, rollback | `run_universe_at_timestamp(session, timestamp, ctx)` wrapper returning `DomainReplayResult` | Yes | No | P0 |
| **ingestion** | run-bars (--timestamp, --dry-run), run-backfill (--start/--end), plan-bars | `run_ingestion_at_timestamp(session, timestamp, ctx)` | No | Yes (ingestion_missing_bars, ingestion_late_bars) | P0 |
| **features** | plan-pipeline (dry-run), run-pipeline (--dry-run), resolve-for-simulation | `run_features_at_timestamp(session, timestamp, dataset_version_id, ctx)` | No | Yes (feature_validation_failure, feature_mixed_lineage) | P0 |
| **research** | run-simulation (--dry-run), run-experiment (--dry-run), plan-experiment, list/inspect experiments | `run_research_at_timestamp(session, timestamp, experiment_config, ctx)` | Yes | Yes (governance_demotion_trigger via scenario) | P1 |
| **strategy** | Mostly read-only: list, inspect, equity-curve, compare, active | `snapshot_strategy_catalog_at_timestamp(session, timestamp, ctx)` — read-only | No | No | P2 |
| **portfolio** | export, snapshot, reconcile, verify-dashboard-state | `snapshot_portfolio_at_timestamp(session, timestamp, ctx)` | No | No | P0 |
| **governance** | promotion scan/run (--enforce), demotion scan/run, transition (--enforce), health run (--persist), export | `run_governance_at_timestamp(session, timestamp, ctx)` | Yes | Yes (governance_demotion_trigger) | P0 |
| **execution** | reconcile-order (--dry-run), external-reconcile (--persist), policy-preview (--offline), submit-intents (--dry-run) | `snapshot_execution_at_timestamp(session, timestamp, ctx)` | No | Yes (broker_reconciliation_mismatch, order_rejected) | P1 |
| **runtime** | plan-cycle (--timestamp), evaluate-cycle (--timestamp, --dry-run), replay, replay-debug, replay-plan | `run_trading_cycle_at_timestamp(session, timestamp, ctx)` returning structured result | No | Yes (runtime_job_failure) | P0 |
| **operations** | health, health-detailed, list-jobs, verify-runtime-soak (--window-start/--window-end), list-alerts | `snapshot_operations_at_timestamp(session, timestamp, ctx)` | No | No | P1 |
| **risk** | snapshot compute (--dry-run/--persist), drawdown evaluate (--dry-run/--persist), budget compute/run, export | `run_risk_at_timestamp(session, timestamp, ctx)` | No | Yes (risk_limit_breach, drawdown_breach) | P0 |
| **platform** | backtest plan/run/inspect/report — all `"not_implemented"` stubs | **This is the orchestrator — implement `PlatformReplayContext` + replay runner here** | N/A | N/A | P0 |
| **api smoke** | dashboard-snapshot (wraps backtesting.handle_read_dashboard) | `validate_api_smoke_after_replay(session, timestamp, ctx)` | No | No | P2 |

---

## Architecture Rule

```
platform replay owns the clock
domains expose handlers/jobs that can be called at timestamp T
```

The platform CLI will orchestrate the replay. **The platform runner must call service functions directly — never shell out to CLI commands.**

---

## Domain Details

---

### admin

**Current capability:**
- `validate-config` / `doctor`: structured preflight checks (config, env, DB, broker) — all read-only, no timestamp
- `inspect-failed-runs` / `inspect-failed-run`: reads RunManifests SOR table, JSON output
- `inspect-audit-log`: `AuditLogService.list_events()`, filterable by date range
- `inspect-db`: connectivity check

**Missing hook:**
```python
def validate_admin_preflight(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> AdminPreflightResult:
    """Read-only DB and config validation. Called before replay starts."""
```
Extract check logic from `handle_validate_config` + `handle_inspect_db` into a service function in `application/services/`. CLI stays as thin wrapper.

**Missing summary:** `admin_preflight_summary` — DB connectivity, alembic version, config validation outcome.

**Timeline events:** Not applicable (preflight runs before replay timeline).

**Failure injection:** Not recommended.

**Tasks:**
- P1: Extract `validate_admin_preflight()` service from CLI handlers so platform runner can call it directly
- P1: Return structured `AdminPreflightResult(db_ok, config_ok, alembic_version, warnings)`
- P2: Add `--timestamp` to `inspect-audit-log` as already-working date filter

---

### diagnostics

**Current capability:**
- `snapshot` calls `RuntimeSnapshotService.capture(sections=...)` returning `RuntimeSnapshot` Pydantic model
- `--output PATH` writes JSON to disk; `--section` filters to single section
- All handlers are read-only

**Missing hook:**
```python
def snapshot_diagnostics_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    sections: frozenset[str] | None = None,
) -> RuntimeSnapshot:
    """Thin wrapper over RuntimeSnapshotService.capture(). No new logic needed."""
```

**Missing summary:** `diagnostics_summary` — `snapshot_timestamp`, `sections_captured`, `portfolio_value`, `active_strategies`, `kill_switch_active`.

**Timeline events:** None needed.

**Failure injection:** Not recommended.

**Tasks:**
- P1: Expose `RuntimeSnapshotService.capture()` as a named service entry point callable without going through CLI
- P1: Include `diagnostics_summary` in platform artifact bundle
- P2: Add `--timestamp` param to `snapshot` command for historical DB snapshot

---

### safety

**Current capability:**
- `emergency-halt`: calls `RuntimeControlService.activate_kill_switch()`, returns structured dict with `canceled_order_count`
- `release-kill-switch`: calls `RuntimeControlService.resume_trading()`
- `pre-trade-check`: has `--bar-timestamp`; stubs out risk state reader
- `startup-check`: emits `KILL_SWITCH_STATE_LOADED` audit event
- `gate-status`: structured gate readiness payload

**Missing hook:**
```python
def apply_safety_event(
    *,
    session: Session,
    timestamp: datetime,
    event: SafetyTimelineEvent,
    replay_context: PlatformReplayContext,
) -> SafetyEventResult:
    """Apply a safety event (halt/release/arm/disarm) with actor/reason/audit trail."""
```

```python
def snapshot_safety_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> SafetySnapshot:
    """Read kill-switch, gate, and environment policy state."""
```

**Missing summary:** `safety_summary` — `kill_switch_enabled`, `live_gate_armed`, `trading_environment`, `recent_safety_events`.

**Timeline events:**
```
safety_emergency_halt        # activate kill switch
safety_release_halt          # release kill switch
live_trading_armed           # arm live gate
live_trading_disarmed        # disarm live gate
safety_startup_check         # emit startup audit
```

**Failure injection:**
- `safety_emergency_halt` — inject a mid-replay halt to test governance/controls response

**Tasks:**
- P0: Extract `apply_safety_event()` service from CLI handlers; CLI remains thin wrapper
- P0: `snapshot_safety_at_timestamp()` wrapping `_build_status_payload()`
- P0: `safety_summary` dataclass for platform artifact
- P1: Add failure injection hook `inject_emergency_halt_at_timestamp()` with `actor`, `reason`, `replay_context`
- P2: Wire process-local arm/disarm to a durable SOR flag so replay can check it

---

### controls

**Current capability:**
- `export`: writes `{"exported_at": ..., "controls": snapshot}` JSON artifact
- `seed --dry-run`: validates fixture without writing
- `verify-runtime-gates`: structured gate checks
- `audit-log`: filters by all control event types
- `state` / `strategy list` / `allocation list`: structured JSON

**Missing hook:**
```python
def apply_controls_event(
    *,
    session: Session,
    timestamp: datetime,
    event: ControlsTimelineEvent,
    replay_context: PlatformReplayContext,
) -> ControlsEventResult:
    """Apply pause/resume/strategy-enable-disable/allocation-override/mode-change."""
```

```python
def snapshot_controls_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> ControlsSnapshot:
    """Read all control state — used by platform runner before each trading cycle."""
```

**Missing summary:** `controls_summary` — `trading_paused`, `kill_switch_active`, `trading_mode`, `strategy_count`, `disabled_strategies`, `active_overrides`.

**Timeline events:**
```
controls_paused              # soft pause
controls_resumed             # resume from pause
trading_enabled              # re-enable trading
trading_disabled             # disable trading
strategy_disabled            # per-strategy disable
strategy_enabled             # per-strategy enable
allocation_override_set      # manual cap applied
allocation_override_cleared  # cap removed
trading_mode_changed         # simulation/paper/live
```

**Failure injection:** Not recommended (controls are operator intent).

**Tasks:**
- P0: Extract `apply_controls_event()` service callable without CLI argparse namespace
- P0: `snapshot_controls_at_timestamp()` wrapping `_controls_snapshot()` + `_strategy_controls_snapshot()`
- P0: `controls_summary` for platform artifact
- P1: Timeline event dispatch with actor/reason/audit evidence on every mutation
- P2: Add `--timestamp` to `export` for time-indexed artifact naming

---

### settings

**Current capability:**
- `snapshot`: writes JSON with SHA256 hash, includes `runtime_config` env variables
- `export`: writes full settings + metadata JSON
- `seed` / `set` with `--dry-run`: validates without writing
- `verify-persisted --expect key=value`: assertion-style checks
- `verify-runtime-effect`: wraps `backtesting.handle_verify_risk_parameter_effects`
- `audit-log`: filters `OPERATOR_SETTINGS_UPDATED` events

**Missing hook:**
```python
def apply_settings_event(
    *,
    session: Session,
    timestamp: datetime,
    event: SettingsTimelineEvent,
    replay_context: PlatformReplayContext,
) -> SettingsEventResult:
    """Apply a settings patch with actor, reason, and audit trail."""
```

```python
def snapshot_settings_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> SettingsSnapshot:
    """Read current operator settings + SHA256 hash for deterministic replay."""
```

**Missing summary:** `settings_summary` — `settings_id`, `sha256`, `risk_tolerance`, `max_drawdown_limit`, `auto_promote_enabled`, `rebalance_frequency`.

**Timeline events:**
```
settings_changed             # patch applied
settings_seeded              # initial seed from fixture
settings_reset_defaults      # factory reset
```

**Failure injection:** Not recommended.

**Tasks:**
- P0: Extract `snapshot_settings_at_timestamp()` from `handle_snapshot` into a service function
- P0: Extract `apply_settings_event()` from `handle_seed` / `handle_set` into service
- P0: `settings_summary` dataclass for platform artifact
- P1: Include settings SHA256 in `PlatformReplayContext` so every tick has a deterministic settings fingerprint
- P2: Add `--timestamp` to `snapshot` to write time-indexed artifact

---

### universe

**Current capability:**
- `select-now --timestamp --dry-run`: calls `run_universe_selection_cycle(cycle_timestamp, dry_run)` — fully timestamp-callable
- `rotate --timestamp --dry-run --skip-cadence-check`: calls `run_universe_rotation()` — timestamp-callable
- `rollback --timestamp --dry-run`: calls `run_universe_rollback()` — timestamp-callable
- `history-for-date --timestamp`: returns active version for that date
- `replay-timeline --start --end`: returns all universe transitions in a window
- `inspect-active --timestamp`, `inspect-symbols --timestamp`: read-only snapshot at timestamp
- `candidate-generate --timestamp --dry-run`: scored candidate without persisting

**Missing hook:**
```python
def run_universe_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    skip_cadence_check: bool = True,
) -> UniverseReplayResult:
    """Wraps run_universe_selection_cycle; returns structured result with version_id, symbol_count."""
```
The underlying jobs are already callable; this is a thin typed wrapper threading `PlatformReplayContext`.

**Missing summary:** `universe_summary` — `active_version_id`, `symbol_count`, `effective_from`, `rotation_count`, `last_churn_pct`.

**Timeline events:**
```
universe_selected            # new version activated from selection cycle
universe_rotated             # rotation applied
universe_rolled_back         # rollback to prior version
universe_seeded              # manual seed from fixture
```

**Failure injection:** Not applicable.

**Tasks:**
- P0: Write `run_universe_at_timestamp()` wrapper; return typed `UniverseReplayResult`
- P0: `universe_summary` for platform artifact
- P1: Use `replay-timeline` output in platform runner to pre-seed the replay schedule with universe change events
- P2: Add `--output` JSON artifact to `rotate` and `rollback`

---

### ingestion

**Current capability:**
- `run-bars --timestamp --dry-run`: calls `run_market_ingestion_cycle(now_utc=timestamp)` — already timestamp-callable
- `plan-bars --timestamp`: structured dry-run with `would_write=False`
- `run-backfill --start --end --dry-run`: structured backfill plan
- `run-corporate-actions --dry-run`: validates source dataset without writing
- Inspection: `inspect-bar`, `inspect-coverage`, `list-incidents`, `list-ingestion-runs`

**Missing hook:**
```python
def run_ingestion_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> IngestionReplayResult:
    """Wraps run_market_ingestion_cycle; returns dataset_version_id, symbols_ingested, incidents."""
```

**Missing summary:** `ingestion_summary` — `latest_raw_dataset_version_id`, `symbols_covered`, `missing_bar_incident_count`, `corporate_actions_ingested`, `date_range`.

**Timeline events:** None needed (ingestion is automatic/scheduled).

**Failure injection:**
- `ingestion_missing_bars` — inject `MissingBarIncidents` rows for specific symbols/dates
- `ingestion_late_bars` — inject bars with `completeness_status='partial'`
- Not appropriate: injecting live broker failures

**Tasks:**
- P0: `run_ingestion_at_timestamp()` typed wrapper
- P0: `ingestion_summary` for platform artifact
- P1: `inject_ingestion_failure(session, timestamp, symbols, failure_type)` — writes fake incident rows
- P2: Add `--output` JSON artifact to `run-bars`

---

### features

**Current capability:**
- `plan-pipeline --dataset-version-id --start-date --end-date --symbols`: structured dry-run with reuse/compute decisions per feature step
- `run-pipeline --dry-run`: same plan without execution; live path calls `run_feature_pipeline_cycle()`
- `resolve-for-simulation`: checks feature availability for a simulation window
- `validate-dataset --check-parquet`: validates a feature version
- `export-lineage`: writes lineage artifact

**Missing hook:**
```python
def run_features_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    dataset_version_id: str,
    symbols: list[str],
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> FeatureReplayResult:
    """Wraps run_feature_pipeline_cycle; returns feature_dataset_version_ids, lineage."""
```
Note: features are indexed by dataset version, not wall-clock timestamp. The platform runner must resolve the correct `dataset_version_id` for the replay bar before calling this.

**Missing summary:** `feature_summary` — per-feature `(feature_name, dataset_version_id, computation_parameters, storage_path)`, `lineage_ok`, `mixed_lineage_warnings`.

**Timeline events:** None (automatic after ingestion).

**Failure injection:**
- `inject_feature_validation_failure(session, dataset_version_id)` — sets `validation_status='failed'`
- `inject_mixed_lineage(session, ...)` — mismatches `underlying_price_basis` to trigger `MixedLineageError`
- Not appropriate: injecting parquet read failures

**Tasks:**
- P0: `run_features_at_timestamp()` typed wrapper resolving dataset version from replay timestamp
- P0: `feature_summary` for platform artifact including lineage chain
- P1: `inject_feature_validation_failure()` for testing downstream degraded-data recovery
- P2: Add `--timestamp` / `--as-of-date` alias to `plan-pipeline`

---

### research

**Current capability:**
- `run-simulation --dry-run`, `--start-date`, `--end-date`: structured plan output
- `run-experiment --dry-run`, staged pipeline support
- `plan-experiment`: expands config to work units without execution
- `list-experiments` / `inspect-experiment` / `cancel-experiment --dry-run`
- `intelligence rank-candidates / cluster-strategies / predict-robustness`: artifact generation

**Missing hook:**
```python
def run_research_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    experiment_config: ExperimentDefinition,
    replay_context: PlatformReplayContext,
) -> ResearchReplayResult:
    """Run or resume a research experiment scoped to a timestamp window."""
```
Note: research spans date ranges, not point-in-time. The platform runner should schedule research events on a calendar (e.g., weekly) rather than every tick.

**Missing summary:** `research_summary` — `experiment_id`, `total_runs`, `passed_filters`, `top_candidates`, `run_timestamp`.

**Timeline events:**
```
research_experiment_started    # new experiment dispatched
research_experiment_completed  # results available
strategy_generated             # new strategy config added to catalog
```

**Failure injection:**
- `governance_demotion_trigger` — inject a strategy with bad metrics into results to trigger auto-demotion

**Tasks:**
- P1: Extract `run_research_at_timestamp()` service callable without CLI namespace
- P1: `research_summary` for platform artifact
- P1: Research timeline event dispatch
- P2: `inject_failing_strategy()` to seed a strategy that will trigger governance demotion

---

### strategy

**Current capability:**
- `list`, `inspect`, `compare`, `equity-curve`, `active`: read-only DB-backed catalog
- `list-types`, `inspect-type`, `validate-config`, `list-components`, `inspect-component`: registry lookups (no DB)
- `evaluate-bar`: deprecated, forwards to `runtime evaluate-cycle`
- `inspect-readiness`: misaligned — checks ingestion readiness, not strategy

**Missing hook:**
```python
def snapshot_strategy_catalog_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> StrategyCatalogSnapshot:
    """Read active strategies, governance state, and current metrics."""
```

**Missing summary:** `strategy_catalog_summary` — `total_active`, `paper_count`, `live_count`, `strategy_types`.

**Timeline events:** Strategy events are owned by governance (promotion/demotion). No separate strategy-domain events.

**Failure injection:** Not applicable.

**Tasks:**
- P2: `snapshot_strategy_catalog_at_timestamp()` thin wrapper over existing catalog service
- P2: Move `inspect-readiness` to `operations` domain
- P2: `strategy_catalog_summary` for platform artifact

---

### portfolio

**Current capability:**
- `snapshot`: full portfolio snapshot via `PortfolioAnalyticsService` + `PortfolioEquityCurveService` + `PortfolioSummaryService`
- `export --output PATH`: writes JSON artifact bundle
- `reconcile`: checks cash+holdings consistency
- `verify-dashboard-state --run-id`: verifies run's cash/position snapshots
- `allocation-config snapshot --output`: writes `AllocationConfig` artifact with hash
- `construction runs/show/verify`: construction batch inspection

**Missing hook:**
```python
def snapshot_portfolio_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> PortfolioSnapshot:
    """Read portfolio state as of the given timestamp's latest snapshots."""
```
Wraps `_portfolio_snapshot(session)` with `PlatformReplayContext` threading.

**Missing summary:** `portfolio_summary` — `portfolio_value`, `cash_balance`, `invested_capital`, `open_positions`, `total_pnl_pct`, `equity_curve_points_count`.

**Timeline events:** Portfolio snapshots happen automatically inside the trading cycle — no explicit platform timeline events needed.

**Failure injection:** Not applicable (portfolio is derived state from fills/cash ledger).

**Tasks:**
- P0: `snapshot_portfolio_at_timestamp()` wrapping `_portfolio_snapshot()`; return `PortfolioSnapshot` Pydantic model
- P0: `portfolio_summary` for platform artifact
- P1: Add `--timestamp` to `export` so artifact is time-indexed
- P2: Add `--as-of-run-id` to `snapshot` to pin to a specific run's snapshots

---

### governance

**Current capability:**
- `promotion run --enforce`: calls `AutoPromotionService.run()`, structured result
- `demotion run --enforce / --dry-run`: calls `AutoDemotionService.run()`, structured `demotions_executed`
- `transition --enforce`: `StrategyGovernanceService.transition()` with actor/role/reason
- `health run --persist`: `StrategyHealthLifecycleService.run()`, structured health transitions
- `export --strategy-id`: full governance bundle (state + health + transitions + audit)
- `audit list/show/chain/supersede`: `GovernanceAuditService`

**Missing hook:**
```python
def run_governance_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> GovernanceReplayResult:
    """Run auto-promotion, auto-demotion, and health lifecycle evaluations for this tick."""
```

```python
def apply_governance_event(
    *,
    session: Session,
    timestamp: datetime,
    event: GovernanceTimelineEvent,
    replay_context: PlatformReplayContext,
) -> GovernanceEventResult:
    """Apply a manual governance transition at a specific timestamp."""
```

**Missing summary:** `governance_summary` — `strategies_evaluated`, `promotions_executed`, `demotions_executed`, `health_transitions`, `strategies_in_breach`.

**Timeline events:**
```
governance_manual_transition        # operator forces a state change
governance_auto_promotion           # auto-promotion fired
governance_auto_demotion            # auto-demotion fired
health_review_acknowledged          # operator clears a suspension
```

**Failure injection:**
- `governance_demotion_trigger` — seed a strategy with metrics below demotion threshold, then run auto-demotion cycle

**Tasks:**
- P0: Extract `run_governance_at_timestamp()` from CLI handler logic into service
- P0: `apply_governance_event()` dispatch for timeline events
- P0: `governance_summary` for platform artifact
- P1: `inject_governance_demotion_trigger(session, strategy_id, timestamp)`
- P1: Ensure all governance mutations record `run_id` from `PlatformReplayContext`

---

### execution

**Current capability:**
- `reconcile-order --dry-run`: structured result with fill + position + cash snapshots
- `external-reconcile --persist`: broker-vs-platform report
- `policy-preview --offline --mid-price`: transforms order intent without broker call
- `submit-intents --dry-run`: reads pending intents without submitting
- `inspect-order`, `inspect-position`, `inspect-cash`: DB-backed read-only
- `validate-broker-consistency`: tolerance-based drift check
- `list-fill-quality --run-id --adverse-only`: structured fill quality report

**Missing hook:**
```python
def snapshot_execution_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> ExecutionSnapshot:
    """Read open orders, fill quality, and reconciliation state for a given run."""
```

**Missing summary:** `execution_summary` — `open_order_count`, `fills_this_cycle`, `adverse_fills`, `reconciliation_status`, `policy_mode`.

**Timeline events:** None needed (execution is automatic inside trading cycle).

**Failure injection:**
- `inject_broker_reconciliation_mismatch(session, run_id, symbol, delta_usd)` — writes a reconciliation row with drift > tolerance
- `inject_order_rejected(session, run_id, symbol, reason)` — creates a tracked order with `status='rejected'`
- Not appropriate: injecting broker API outages in replay (no live broker in replay)

**Tasks:**
- P1: `snapshot_execution_at_timestamp()` wrapping existing inspection services
- P1: `execution_summary` for platform artifact
- P1: `inject_broker_reconciliation_mismatch()` failure hook
- P2: `inject_order_rejected()` failure hook

---

### runtime

**Current capability:**
- `plan-cycle --timestamp`: resolves window, controls state, universe — fully read-only
- `evaluate-cycle --timestamp --dry-run`: full evaluation cycle with dry-run guard
- `run-cycle --timestamp --dry-run`: trading cycle dispatch
- `trigger-job --dry-run`: registry-aware job dispatch
- `replay --start --end --symbols --cycles`: `ReplayRuntimeService.run()` — full replay
- `replay-debug --start --end`: `RuntimeReplayDebugRunner` — deterministic no-broker replay
- `replay-plan`: dry-run with estimated ticks and intended writes
- `calendar-status --timestamp`: market phase + next open/close
- `rescue-orphans --dry-run`: orphan job recovery

**Missing hook:**
```python
def run_trading_cycle_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> TradingCycleReplayResult:
    """Run one trading cycle at timestamp T. Wraps run_trading_cycle(now_utc=timestamp)."""
```
This is the core clock tick of the platform replay runner.

**Missing summary:** `runtime_summary` — `ticks_attempted`, `ticks_ok`, `ticks_failed`, `total_orders`, `total_fills`, `run_ids`.

**Timeline events:** None needed (runtime owns the clock).

**Failure injection:**
- `inject_runtime_job_failure(session, job_name, timestamp)` — writes a `RuntimeJobRuns` row with `status='failed'`

**Tasks:**
- P0: `run_trading_cycle_at_timestamp()` typed wrapper returning `TradingCycleReplayResult`
- P0: `runtime_summary` for platform artifact
- P0: Wire `PlatformReplayContext.run_id` through to `run_trading_cycle()` so all artifacts are grouped
- P1: `inject_runtime_job_failure()` for testing recovery paths
- P2: `replay-plan` should call domain dry-run functions and aggregate their write profiles

---

### operations

**Current capability:**
- `health` / `health-detailed`: `SystemHealthService` + `DetailedSystemHealthService` — structured JSON
- `list-jobs` / `list-job-runs`: `OperationsService` wrappers
- `verify-runtime-soak --window-start --window-end`: `RuntimeSoakVerificationService`, can persist report
- `list-alerts` / `acknowledge-alert` / `resolve-alert` / `snooze-alert`: `OperationalAlertService`
- `inspect-ingestion-readiness --timestamp`: `check_ingestion_readiness_job()`
- `runbook list/show`: filesystem-based discovery

**Missing hook:**
```python
def snapshot_operations_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> OperationsSnapshot:
    """Read system health, recent job statuses, and active alerts at this timestamp."""
```

**Missing summary:** `operations_summary` — `system_health_status`, `active_alerts_count`, `critical_alerts_count`, `jobs_failed_last_cycle`.

**Timeline events:** None needed (observability/reporting, not event-driven).

**Failure injection:** Not recommended.

**Tasks:**
- P1: `snapshot_operations_at_timestamp()` wrapping existing services
- P1: `operations_summary` for platform artifact
- P2: `verify-runtime-soak` wired to `PlatformReplayContext.run_id` as the soak window identifier

---

### risk

**Current capability:**
- `snapshot compute --dry-run / --persist`: `RiskSnapshotService.compute_snapshot()`
- `drawdown evaluate --dry-run / --persist`: `DrawdownGovernanceService.run()`
- `budget compute --dry-run`: `RiskBudgetingService.compute()` with rollback
- `factor run --dry-run`: `FactorExposureMonitoringService.run()` with rollback
- `pretrade check`: pre-trade risk evaluation
- `export --output`: writes limits + snapshot + drawdown states + budget

**Missing hook:**
```python
def run_risk_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> RiskReplayResult:
    """Compute risk snapshot, evaluate drawdown ladder, and run budget cycle."""
```

```python
def snapshot_risk_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> RiskSnapshot:
    """Read latest risk state without computing new values."""
```

**Missing summary:** `risk_summary` — `gross_exposure`, `net_exposure`, `drawdown_pct`, `is_blocked`, `block_reasons`, `drawdown_ladder_states`, `budget_mode`.

**Timeline events:** None needed (automatic monitoring).

**Failure injection:**
- `inject_risk_limit_breach(session, run_id, timestamp, breach_type)` — writes `RiskSnapshotRow` with `is_blocked=True`
- `inject_drawdown_breach(session, strategy_id, realized_drawdown)` — writes `DrawdownGovernanceLadderState` above threshold
- Not appropriate: injecting sector data corruption

**Tasks:**
- P0: `run_risk_at_timestamp()` typed wrapper
- P0: `snapshot_risk_at_timestamp()` for pre-cycle checks
- P0: `risk_summary` for platform artifact
- P1: `inject_risk_limit_breach()` failure hook
- P1: `inject_drawdown_breach()` failure hook — tests governance→controls chain
- P2: Ensure `--as-of` on `snapshot compute` flows correctly through `PlatformReplayContext`

---

### platform (orchestrator)

**Current capability:**
- `backtest plan / run / inspect / report`: **ALL return `"status": "not_implemented"` stubs**
- `fixture seed`: wraps `backtesting.handle_seed_fixture`
- `dashboard-snapshot`: wraps `backtesting.handle_read_dashboard`

**What needs to be built here:**

```python
@dataclass
class PlatformReplayContext:
    run_id: UUID
    replay_id: str
    timestamp: datetime
    symbols: list[str]
    actor: str
    dry_run: bool
    artifact_dir: Path | None

@dataclass
class DomainReplayResult:
    domain: str
    timestamp: datetime
    run_id: str
    status: Literal["ok", "skipped", "failed", "dry_run"]
    warnings: list[str]
    errors: list[str]
    artifact_refs: list[str]
    summary: dict[str, Any]

class PlatformBacktestRunner:
    def run(self, inputs: PlatformBacktestInputs) -> PlatformBacktestArtifact:
        # 1. admin preflight
        # 2. settings snapshot (hash into context)
        # 3. controls snapshot
        # 4. safety snapshot
        # FOR EACH TICK in calendar:
        #   5. run_ingestion_at_timestamp(session, tick, ctx)
        #   6. run_features_at_timestamp(session, tick, dataset_version_id, ctx)
        #   7. run_universe_at_timestamp(session, tick, ctx)   [on rotation days]
        #   8. run_trading_cycle_at_timestamp(session, tick, ctx)
        #   9. run_risk_at_timestamp(session, tick, ctx)
        #   10. run_governance_at_timestamp(session, tick, ctx)
        # END TICKS
        # 11. snapshot_portfolio_at_timestamp(session, end, ctx)
        # 12. snapshot_operations_at_timestamp(session, end, ctx)
        # 13. build artifact bundle → PlatformBacktestArtifact
```

**Tasks:**
- P0: Define `PlatformReplayContext` in `contracts/runtime/`
- P0: Define `DomainReplayResult` dataclass
- P0: Implement `PlatformBacktestRunner` in `application/services/platform_backtest_service.py`
- P0: Implement `handle_backtest_run` in `platform.py` calling the service (replace stub)
- P0: Implement `handle_backtest_plan` calling each domain's dry-run in sequence
- P0: Implement `handle_backtest_inspect` reading run manifests + artifact bundle
- P1: Implement `handle_backtest_report` generating CI-friendly summary from artifact
- P1: Add `platform backtest replay-events` to inject timeline events from a YAML schedule

---

## P0/P1/P2 Implementation Roadmap

### P0 — Required for `platform backtest run` MVP

| # | Domain | Task |
|---|--------|------|
| 1 | **platform** | Define `PlatformReplayContext` + `DomainReplayResult` contracts |
| 2 | **platform** | Implement `PlatformBacktestRunner` service + replace stubs in `platform.py` |
| 3 | **runtime** | `run_trading_cycle_at_timestamp()` typed service wrapper |
| 4 | **ingestion** | `run_ingestion_at_timestamp()` typed wrapper over `run_market_ingestion_cycle()` |
| 5 | **features** | `run_features_at_timestamp()` typed wrapper resolving dataset version from timestamp |
| 6 | **universe** | `run_universe_at_timestamp()` typed wrapper |
| 7 | **risk** | `run_risk_at_timestamp()` + `snapshot_risk_at_timestamp()` |
| 8 | **governance** | `run_governance_at_timestamp()` + `apply_governance_event()` |
| 9 | **portfolio** | `snapshot_portfolio_at_timestamp()` |
| 10 | **controls** | `apply_controls_event()` + `snapshot_controls_at_timestamp()` |
| 11 | **settings** | `apply_settings_event()` + `snapshot_settings_at_timestamp()` |
| 12 | **safety** | `apply_safety_event()` + `snapshot_safety_at_timestamp()` |
| 13 | **all** | `*_summary` dataclasses for every domain's platform artifact contribution |

### P1 — Good debugging and reporting

| # | Domain | Task |
|---|--------|------|
| 14 | **admin** | Extract `validate_admin_preflight()` service |
| 15 | **diagnostics** | Expose `RuntimeSnapshotService.capture()` as platform-callable |
| 16 | **operations** | `snapshot_operations_at_timestamp()` wrapper |
| 17 | **execution** | `snapshot_execution_at_timestamp()` + `execution_summary` |
| 18 | **research** | `run_research_at_timestamp()` service callable |
| 19 | **risk** | `inject_risk_limit_breach()` + `inject_drawdown_breach()` failure hooks |
| 20 | **ingestion** | `inject_ingestion_failure()` failure hook |
| 21 | **governance** | `inject_governance_demotion_trigger()` failure hook |
| 22 | **execution** | `inject_broker_reconciliation_mismatch()` failure hook |
| 23 | **runtime** | `inject_runtime_job_failure()` failure hook |
| 24 | **platform** | `platform backtest report` from artifact bundle |

### P2 — Nice-to-have / pitch-deck support

| # | Domain | Task |
|---|--------|------|
| 25 | **strategy** | `snapshot_strategy_catalog_at_timestamp()` read-only wrapper |
| 26 | **features** | `inject_feature_validation_failure()` failure hook |
| 27 | **execution** | `inject_order_rejected()` failure hook |
| 28 | **platform** | `platform backtest replay-events` — inject timeline events from YAML schedule |
| 29 | **universe** | Add `--output` JSON to `rotate` and `rollback` |
| 30 | **operations** | `verify-runtime-soak` wired to `PlatformReplayContext.run_id` |
| 31 | **strategy** | Move `inspect-readiness` from strategy to operations domain |

---

## Key Design Constraints

1. **Never shell out to CLI commands from the platform runner.** Call service functions directly.
2. **All mutation events must record `actor`, `reason`, and `run_id`** from `PlatformReplayContext`.
3. **All replay outputs must include** `timestamp`, `run_id`, `domain`, `status`, `warnings`, `errors`, and `artifact_refs`.
4. **`platform.py` orchestrator stays thin** — domain logic lives in service classes under `application/services/`.
5. **CLI handlers remain thin wrappers** — extract service functions, don't duplicate logic in both.
6. **Features use dataset version IDs, not timestamps directly** — the platform runner must resolve the correct dataset version from the replay bar before calling feature hooks.
7. **Research is calendar-scheduled** (weekly/monthly), not per-tick — the replay schedule dispatches research events at configured intervals.
