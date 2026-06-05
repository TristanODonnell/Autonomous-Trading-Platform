# Platform Replay Fixture Suite

Structured scenario configs for the `atp platform backtest` CLI.
All configs target the historical backtest replay runner (not paper trading).

---

## Directory Structure

```
replays/
  base/                  Canonical healthy baseline — compare all others against this
  short/                 Fast smoke tests for post-change validation
  timeline_events/       One focused config per event category
  interactions/          Multi-event cross-domain realistic scenarios
  failure_injection/     Intentional failure injection per domain + combined
  medium/                2-month window — cadence and multi-cycle validation
  long/                  6-month regression and full-year demo
```

---

## Config Index

### Base

| Config | Timespan | Trading Days | Purpose | Recommended Use |
|--------|----------|-------------|---------|-----------------|
| `base/base_platform_replay.yaml` | 2024-01-02 → 2024-01-15 | 10 | Canonical healthy baseline; all jobs, no events | baseline |

### Short (Smoke Tests)

| Config | Timespan | Trading Days | Purpose | Recommended Use |
|--------|----------|-------------|---------|-----------------|
| `short/smoke_healthy.yaml` | 2024-01-02 → 2024-01-08 | 5 | Full loop, core jobs, no events | smoke |
| `short/smoke_minimal.yaml` | 2024-01-02 → 2024-01-04 | 3 | Bare minimum; SPY only, 3 jobs | smoke |

### Timeline Events

| Config | Timespan | Trading Days | Events | Event Types |
|--------|----------|-------------|--------|-------------|
| `timeline_events/settings_change.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `settings_changed` |
| `timeline_events/controls_lifecycle.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `controls_paused`, `controls_resumed` |
| `timeline_events/strategy_lifecycle.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `strategy_disabled`, `strategy_enabled` |
| `timeline_events/allocation_override.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `allocation_override_set`, `allocation_override_cleared` |
| `timeline_events/governance_transitions.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `governance_manual_transition`, `health_review_acknowledged` |
| `timeline_events/safety_events.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `safety_emergency_halt`, `safety_release_halt` |

Each config tests one event category in isolation and is sized to answer:
- Did the event apply at the right timestamp?
- Did it mutate expected state?
- Is the change visible in the artifact summary?
- Did downstream cycles react?

### Interactions

| Config | Timespan | Trading Days | Events | Domains |
|--------|----------|-------------|--------|---------|
| `interactions/settings_then_controls.yaml` | 2024-01-02 → 2024-02-29 | 43 | 5 | settings + controls |
| `interactions/governance_and_allocation.yaml` | 2024-01-02 → 2024-02-29 | 43 | 5 | governance + controls + allocation |
| `interactions/safety_with_recovery.yaml` | 2024-01-02 → 2024-02-15 | 33 | 5 | safety + settings + governance |

### Failure Injection

> **All failure injection configs require `--inject-failures` at runtime.**
> Without this flag, failure injection events are silently skipped.

| Config | Timespan | Trading Days | Injections | Domains |
|--------|----------|-------------|-----------|---------|
| `failure_injection/ingestion_failures.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `ingestion` |
| `failure_injection/risk_failures.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `risk` |
| `failure_injection/governance_failures.yaml` | 2024-01-02 → 2024-01-31 | 23 | 1 | `governance` |
| `failure_injection/execution_failures.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `execution` |
| `failure_injection/runtime_failures.yaml` | 2024-01-02 → 2024-01-31 | 23 | 2 | `runtime` |
| `failure_injection/combined_failures.yaml` | 2024-01-02 → 2024-02-29 | 43 | 5 | ingestion + risk + governance + execution + runtime |

**Supported injection kinds:**

| Kind | Target | SOR Effect |
|------|--------|------------|
| `missing_bars` | `ingestion` | Writes `MissingBarIncidents` rows |
| `late_bars` | `ingestion` | Writes `MissingBarIncidents` rows (late_bar type) |
| `feature_validation_failure` | `features` | Sets `validation_status=failed` on feature dataset version |
| `risk_limit_breach` | `risk` | Writes `RiskSnapshot` with `is_blocked=true` |
| `drawdown_breach` | `risk` | Updates `DrawdownGovernanceLadderState` to `breached` |
| `governance_demotion_trigger` | `governance` | Seeds bad `MetricsSummary` to trigger auto-demotion |
| `broker_reconciliation_mismatch` | `execution` | Writes `AuditLogEvent` with `BROKER_RECONCILIATION_MISMATCH` |
| `order_rejected` | `execution` | Writes `AuditLogEvent` with `ORDER_REJECTED` |
| `runtime_job_failure` | `runtime` | Writes `RuntimeJobRuns` row with `status=failed` |

### Medium

| Config | Timespan | Trading Days | Events | Recommended Use |
|--------|----------|-------------|--------|-----------------|
| `medium/two_month_healthy.yaml` | 2024-01-02 → 2024-02-29 | 43 | 0 | integration |
| `medium/two_month_with_events.yaml` | 2024-01-02 → 2024-02-29 | 43 | 8 | integration |

### Long

| Config | Timespan | Trading Days | Events | Recommended Use |
|--------|----------|-------------|--------|-----------------|
| `long/six_month_regression.yaml` | 2024-01-02 → 2024-06-28 | ~130 | 7 | regression |
| `long/full_year_demo.yaml` | 2024-01-02 → 2024-12-31 | ~261 | 12 | demo |

---

## Supported Timeline Event Types

| Category | Event Type |
|----------|------------|
| Controls | `controls_paused`, `controls_resumed`, `trading_enabled`, `trading_disabled`, `strategy_disabled`, `strategy_enabled`, `allocation_override_set`, `allocation_override_cleared`, `trading_mode_changed` |
| Settings | `settings_changed`, `settings_seeded`, `settings_reset_defaults` |
| Governance | `governance_manual_transition`, `governance_auto_promotion`, `governance_auto_demotion`, `health_review_acknowledged` |
| Safety | `safety_emergency_halt`, `safety_release_halt`, `live_trading_armed`, `live_trading_disarmed`, `safety_startup_check` |
| Injection | `failure_injected` (requires `--inject-failures`) |

---

## Supported Scheduled Jobs

| Job | Cadence Options |
|-----|----------------|
| `ingestion` | `daily` |
| `features` | `daily`, `after_ingestion` |
| `trading_cycle` | `daily` |
| `risk` | `daily` |
| `governance` | `daily` |
| `portfolio_snapshot` | `daily` |
| `universe` | `monthly`, `weekly` |
| `research` | `weekly` |
| `operations_health` | `daily` |
| `dashboard_snapshot` | `daily` |

Allowed cadences: `daily`, `weekly`, `monthly`, `after_ingestion`, `hourly`, `per_tick`

---

## CLI Usage

### Plan (validation only, no mutations)

```bash
atp platform backtest plan \
  --fixture fixtures/platform/replays/short/smoke_healthy.yaml
```

Override fixture values from CLI (CLI wins over fixture):
```bash
atp platform backtest plan \
  --fixture fixtures/platform/replays/base/base_platform_replay.yaml \
  --symbols SPY,MSFT \
  --start 2024-02-01 \
  --end 2024-03-01
```

### Run

```bash
# Smoke test — run after any platform change
atp platform backtest run \
  --fixture fixtures/platform/replays/short/smoke_healthy.yaml \
  --output artifacts/platform/backtests/smoke_healthy.json

# Minimal smoke (fastest)
atp platform backtest run \
  --fixture fixtures/platform/replays/short/smoke_minimal.yaml \
  --output artifacts/platform/backtests/smoke_minimal.json

# Timeline event tests
atp platform backtest run \
  --fixture fixtures/platform/replays/timeline_events/controls_lifecycle.yaml \
  --output artifacts/platform/backtests/controls_lifecycle.json

# Failure injection (requires --inject-failures)
atp platform backtest run \
  --fixture fixtures/platform/replays/failure_injection/ingestion_failures.yaml \
  --inject-failures \
  --output artifacts/platform/backtests/ingestion_failures.json

# Interaction scenario
atp platform backtest run \
  --fixture fixtures/platform/replays/interactions/safety_with_recovery.yaml \
  --output artifacts/platform/backtests/safety_with_recovery.json

# Medium integration run
atp platform backtest run \
  --fixture fixtures/platform/replays/medium/two_month_with_events.yaml \
  --output artifacts/platform/backtests/two_month_with_events.json

# Long regression
atp platform backtest run \
  --fixture fixtures/platform/replays/long/six_month_regression.yaml \
  --output artifacts/platform/backtests/six_month_regression.json

# Dry run (no mutations — prints plan)
atp platform backtest run \
  --fixture fixtures/platform/replays/long/full_year_demo.yaml \
  --dry-run
```

### Inspect

```bash
# Summary view (no tick_results)
atp platform backtest inspect \
  --artifact artifacts/platform/backtests/smoke_healthy.json

# Specific section
atp platform backtest inspect \
  --artifact artifacts/platform/backtests/two_month_with_events.json \
  --section timeline_events_applied

# By run_id (searches artifacts/platform/backtests/)
atp platform backtest inspect \
  --run-id replay-abc123def456
```

### Report

```bash
atp platform backtest report \
  --artifact artifacts/platform/backtests/smoke_healthy.json
```

---

## Notes on `initial_state`

The `initial_state` block in each fixture is **documentation only**. The platform runner
does not apply it automatically. To seed the DB before a replay run, use:

```bash
atp platform fixture seed --fixture <your-seed-fixture.yaml> --dry-run
atp platform fixture seed --fixture <your-seed-fixture.yaml>
```

Domain-specific seed fixtures live in `fixtures/` (controls.yaml, settings.yaml, etc.).

---

## Suggested Testing Workflow

1. **After any platform change:** run `smoke_minimal` → `smoke_healthy`
2. **After timeline event changes:** run the relevant `timeline_events/` config
3. **After failure injection changes:** run the relevant `failure_injection/` config with `--inject-failures`
4. **Before merge:** run `medium/two_month_with_events` + `failure_injection/combined_failures`
5. **For regression baseline:** run `long/six_month_regression`
6. **For demos/visualizer:** run `long/full_year_demo`

---

## Known Gaps / Future Scenarios

- `feature_validation_failure` injection requires a feature dataset version row to exist in SOR; may return `skipped_no_row` if features have not run yet on the target date.
- `drawdown_breach` injection uses the first symbol as a proxy strategy_id; may not match SOR strategy records if those are not seeded.
- `broker_reconciliation_mismatch` and `order_rejected` require a `run_id` to be available from the trading cycle; injection may silently skip if no run has started.
- `trading_mode_changed` event type is supported in schema but not yet tested in isolation — add `timeline_events/trading_mode_change.yaml` when tested.
- `research` domain hook is P2 (supplemental); research-specific failure injection is not yet implemented.
