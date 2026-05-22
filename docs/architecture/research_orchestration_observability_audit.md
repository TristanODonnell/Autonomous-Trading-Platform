# Research Orchestration Observability Audit

**Date:** 2026-05-22
**Branch:** strategy-simulator-experiments-features-updates
**Scope:** Research orchestration layer — gaps vs existing platform observability

---

## 1. Executive Summary

The platform has a **mature observability infrastructure**: OpenTelemetry + Prometheus + Tempo + Loki are fully deployed and configured. The scheduler cycle layer (`run_experiment_pipeline_cycle`) already has well-structured metrics, spans, and logs. The `observability/lifecycle.py` helpers provide reusable `record_cycle_*` / `record_step_*` / `record_job_*` patterns that combine structured logging + OTel metrics + span status in a single call.

The **research orchestration layer is almost entirely dark** inside that boundary. The cycle wrapper fires when an experiment starts and ends, but everything inside the pipeline — stages, parallel execution, checkpoints, cache, validation, regime analysis, intelligence — emits no metrics and no spans, and logs only a handful of basic counts.

**Coverage summary:**

| Signal | Cycle wrapper | Pipeline runner | Stages | Parallel exec | Checkpoints | Cache | Validation | Regime/Intel |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Structured logs | ✅ | ⚠️ partial | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| OTel metrics | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| OTel spans/traces | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 2. Existing Observability: What Is Already There

### 2.1 Infrastructure (fully operational)

| Component | Details |
|-----------|---------|
| OTel Collector | `infra/observability/otel-collector-config.yaml` — OTLP gRPC 4317 / HTTP 4318; exports to Tempo (traces), Loki (logs), Prometheus (metrics) |
| Prometheus | `infra/observability/prometheus-config.yaml` — scrapes `otel-collector:9464`, 5 s interval |
| Grafana | `infra/observability/grafana/provisioning/dashboards/` — RATP folder provisioned, 30 s reload |
| OTel SDK | `observability/tracing.py` — `start_span()` with standard RATP attributes (`ratp.run_id`, `ratp.correlation_id`, `ratp.strategy_id`, `ratp.dataset_version`, `ratp.universe_version`) |
| Metrics SDK | `observability/metrics.py` — single `meter = get_meter("autonomous_trading_platform")`, all counters/histograms registered here |
| Structured logging | `observability/log_context.py` — `LogContext` dataclass (40+ fields), `to_extra()` for stdlib `extra=` injection |
| Runtime context | `observability/runtime_context.py` — `ContextVar` propagating `run_id`, `correlation_id`, `strategy_id`, `dataset_version` across threads |
| Lifecycle helpers | `observability/lifecycle.py` — `record_cycle_started/completed/failed`, `record_step_started/completed/failed`, `record_job_started/completed/failed`, `record_operation_*` |

### 2.2 Experiment pipeline cycle — scheduler boundary (well-instrumented)

**File:** `scheduler/cycles/run_experiment_pipeline_cycle.py`

Metrics registered in `observability/metrics.py`:

```
ratp_experiment_pipeline_cycle_runs_total         labels: environment, component, status
ratp_experiment_pipeline_cycle_failures_total     labels: environment, component, failure_class
ratp_experiment_pipeline_cycle_duration_seconds   labels: environment, component, status
ratp_experiment_pipeline_cycle_step_runs_total    labels: environment, component, step, status
ratp_experiment_pipeline_cycle_step_duration_seconds  labels: environment, component, step, status
```

OTel spans:
- `experiment_pipeline_cycle.run` — wraps full experiment execution, attributes: `ratp.run_id`, `ratp.component`, `ratp.experiment_id`, `ratp.dataset_version`
- `experiment_pipeline_cycle.run_experiment` — per-step wrapper, attribute: `ratp.step`

Run manifests (`RunManifest`) record: `run_id`, `run_type`, `strategy_id`, `strategy_version`, `strategy_config`, `capital_bucket`, `dataset_version`, `universe_version`, `universe_member_count`, `git_commit`, `python_version`, `status`, `current_step`, `last_successful_step`, `error_message`, `artifact_manifest`, `governance_state`.

Runtime job rows (`RuntimeJobRun`) record: `job_run_id`, `job_name`, `parent_job_run_id`, `status`, `trigger_type`, `started_at`, `completed_at`, `duration_ms`, `error_message`, `correlation_id`, `input_summary_json`, `output_summary_json`.

### 2.3 Pipeline runner (partial)

**File:** `research/pipeline/pipeline_runner.py`

Logs only:
- `"Pipeline starting | %d stages | %d initial strategies"` — info
- `"Pipeline stopping early before stage '%s' — no survivors remaining."` — warning
- `"Stage %-16s complete | %d→%d survivors"` — info (n_entered, n_passed)
- `"Pipeline complete | %d stages ran | %d final survivors | %d total sim runs"` — info

No metrics. No spans. No structured `LogContext` injection. Survivor counts are logged as freeform text, not structured fields.

### 2.4 Simulation stage (partial)

**File:** `research/pipeline/stages/simulation_stage.py`

Some inline logs: empty survivor list warning, stage-complete summary (entered/passed/failed counts, strategy-level score details). No metrics. No spans. No checkpoint event correlation.

### 2.5 Walk-forward, Monte Carlo stages

**Files:** `research/pipeline/stages/walk_forward_stage.py`, `monte_carlo_stage.py`

No logging at fold or trial boundaries. No metrics. No spans.

### 2.6 Checkpoint service (silent state machine)

**File:** `research/checkpoints/research_checkpoint_service.py`

The state machine is thorough: statuses `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`, `CACHE_HIT` are persisted with timestamps, `artifact_uri`, `cache_key`, `cached_run_id`, `error_message`, and a `metadata` dict. Restart plans are computed. **None of these state transitions emit logs, metrics, or spans.**

### 2.7 Cache (internal counters, not exported)

**File:** `research/cache/simulation_result_cache.py`

The cache maintains internal `_hits` and `_misses` counters. These are never published to the OTel metrics system. `strategy_generation_cache.py` has no counters at all. No cache event logs. No lookup spans.

### 2.8 Validation, regime analysis, intelligence (silent)

**Files:**
- `research/validation/validation_orchestrator.py`
- `research/analysis/regimes/regime_analysis_service.py`
- `research/intelligence/research_intelligence_service.py`

All three define a `logger` but emit almost no calls. No metrics. No spans. Their intermediate results (robustness scores, regime coverage, ranking distributions) are persisted to Parquet artifacts but never surfaced as observable signals.

### 2.9 Parallel execution service (completely dark)

**File:** `research/execution/parallel_execution_service.py`

`ParallelExecutionService` runs serial or threaded execution of `ExecutionUnit` instances. No logging at mode selection, no logging per unit, no metrics for unit count / success / failure counts, no spans, no propagation of `runtime_context` into threads.

---

## 3. Coverage Matrix

### 3.1 Research run lifecycle

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Experiment start/end | ✅ Cycle-level span + metrics + logs (scheduler) | Run manifest and job row state changes not emitted as OTel events |
| Stage start/complete/fail | ⚠️ pipeline_runner logs counts as freeform text | No structured LogContext fields; no counter/histogram; no span per stage |
| Stage survivor counts | ⚠️ logged as text | Not recorded as metric labels; cannot aggregate across runs |
| Pipeline early-stop | ⚠️ logged as text warning | No metric counter for early-stop events |
| Experiment wall-clock | ✅ `experiment_pipeline_cycle_duration_seconds` | Not broken down by stage |

### 3.2 Pipeline stages

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Simulation stage filter verdicts | ⚠️ logs passed/failed strategy details at DEBUG level | No counter (`research_stage_pass_total`, `research_stage_fail_total`); no score histogram |
| Walk-forward fold start/end | ❌ nothing | Fold N/M boundary events; fold-level pass/fail; train vs test degradation |
| Walk-forward fold duration | ❌ nothing | Per-fold histogram |
| Monte Carlo trial start/end | ❌ nothing | Trial N/M boundary; stability metric per trial |
| Monte Carlo pass rate | ❌ nothing | `min_pass_rate` threshold check outcome |
| Score distributions | ❌ nothing | Sharpe/return/drawdown contributor histograms |

### 3.3 Parallel execution

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Execution mode selected | ❌ | Log event: `parallel_execution_started` with mode, max_workers, unit_count |
| Unit success/failure counts | ❌ | Counter: `research_parallel_unit_total{status=success/failed}` |
| Unit execution duration | ❌ | Histogram: `research_parallel_unit_duration_seconds` |
| Fail-fast trigger | ❌ | Log + counter |
| Thread context propagation | ❌ | `runtime_context` ContextVar not copied into worker threads |
| Queue depth | ❌ | `ThreadPoolExecutor` queue saturation not observable |

### 3.4 Checkpoints and restart

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Checkpoint state transitions | ⚠️ persisted to DB silently | No log at RUNNING/COMPLETED/FAILED/CACHE_HIT; no counter |
| Cache hit vs miss per task type | ⚠️ stored in DB status field | Not exported as metric; cannot build hit-rate dashboard |
| Skipped unit count per restart | ⚠️ restart plan computed in memory | Plan not logged; skipped_count not metered |
| Failed unit root cause | ⚠️ `error_message` stored in DB | Not emitted as structured log; not queryable in Loki |
| Checkpoint duration by task type | ❌ | No histogram for time from PENDING→COMPLETED by `task_type` |

### 3.5 Caching

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Simulation cache hit/miss | ⚠️ internal `_hits`/`_misses` counters | Not exported as OTel metrics; cannot graph hit rate |
| Generation cache hit/miss | ❌ no counters at all | Needs counter creation and export |
| Cache invalidation reason | ❌ | No log or metric when lineage fields change |
| Cache size / eviction | ❌ | No gauge for cache entry count |
| Lookup duration | ❌ | No histogram for cache lookup latency |

### 3.6 Validation, regime analysis, intelligence

| Area | Existing observability | Gap |
|------|----------------------|-----|
| Validation stage start/end | ❌ | Log + counter per stage (`survivorship_validation`, `overfitting_analysis`, etc.) |
| Validation pass/fail per stage | ❌ | Counter: `research_validation_stage_pass_total{stage}` |
| Robustness score distribution | ❌ | Histogram: `research_robustness_score` |
| Validation duration by stage | ❌ | Histogram: `research_validation_duration_seconds{stage}` |
| Regime dimension coverage | ❌ | Gauge: `research_regime_dimension_coverage_pct{dimension}` |
| Regime transition count | ❌ | Counter: `research_regime_transition_total{dimension}` |
| Intelligence ranking scores | ❌ | Histogram: `research_intelligence_ranking_score` |
| Cluster count | ❌ | Gauge: `research_intelligence_cluster_count` |
| Overfitting estimate | ❌ | Histogram: `research_intelligence_overfit_estimate` |

### 3.7 Grafana / dashboards

| Area | Existing | Gap |
|------|---------|-----|
| Dashboard provisioning | ✅ `infra/observability/grafana/provisioning/dashboards/dashboards.yaml` — RATP folder wired | No research-specific dashboard JSON files |
| Research pipeline metrics | ❌ no dashboard | Pipeline funnel (survivors per stage), stage duration breakdown |
| Cache hit-rate panel | ❌ | Single stat: cache hit% for simulation + generation caches |
| Validation/regime/intel panels | ❌ | Robustness score histogram, regime coverage heatmap |
| Alert rules | ⚠️ Prometheus alert rule path configured but no research-specific alert files | Alert on zero survivors, high filter failure rate, stalled experiment |

---

## 4. What Is Missing Specifically for Large Research Runs

When running experiments with hundreds of strategy candidates across multiple pipeline stages, the following questions are currently unanswerable from observability alone:

1. **Which stage is the bottleneck?** Stage duration is not metered; Grafana cannot show a breakdown.
2. **What is the cache reuse rate?** `_hits`/`_misses` exist internally but are never exported.
3. **How many units ran in parallel vs serial?** Execution mode and worker count are invisible.
4. **Why did strategy X fail in stage Y?** Filter verdicts exist in logs but with no structured fields queryable in Loki.
5. **How far through a restart did we get?** Skipped unit count from checkpoint restart is not metered.
6. **Is the validation rejecting everything?** No pass/fail counter exists per validation stage.
7. **Are regime buckets sparsely populated?** Regime dimension coverage is computed but never surfaced.
8. **Which ML features drive the robustness prediction?** Intelligence service reasoning is completely opaque.
9. **How long did a full experiment take end-to-end vs per-stage?** Only cycle-level duration exists.
10. **Did fail-fast fire and kill the experiment early?** No event or metric records this.

---

## 5. Reuse Points for TASK-3.5B

TASK-3.5B must **not** build new infrastructure. All of the following already exist and should be called from research code:

| Reuse point | Location | How to use |
|-------------|---------|-----------|
| `record_step_started/completed/failed` | `observability/lifecycle.py` | Wrap each pipeline stage with `StepMetricSet(runs=..., duration=...)` from `metrics.py` |
| `record_operation_started/completed/failed` | `observability/lifecycle.py` | Wrap validation stages, regime analysis, intelligence service calls |
| `start_span()` | `observability/tracing.py` | Add child spans inside stages; attach `strategy_id`, `stage_name`, fold/trial index as attributes |
| `LogContext` | `observability/log_context.py` | Replace freeform `logger.info("%s | %d", ...)` calls with `logger.info(event, extra=LogContext(...).to_extra())` |
| `meter` | `observability/metrics.py` | Register new research counters/histograms alongside existing `experiment_pipeline_cycle_*` metrics |
| `get_runtime_context()` | `observability/runtime_context.py` | Copy context into thread workers in `ParallelExecutionService._run_parallel()` |
| Grafana provisioning path | `infra/observability/grafana/provisioning/dashboards/` | Drop new dashboard JSON into the RATP folder — auto-loaded by Grafana |

---

## 6. Non-Goals for TASK-3.5B

- Do not change `observability/telemetry.py`, `otel-collector-config.yaml`, or Prometheus configuration.
- Do not redesign the `lifecycle.py` helper signatures or `LogContext` schema.
- Do not add observability to live-trading paths (`execution/`, `safety/`, `scheduler/cycles/run_trading_cycle.py`).
- Do not build dashboards unless metric names are confirmed and metrics are actually emitted.
- Do not instrument simulation math internals (indicator calculation, return computation) — only orchestration boundaries.
- Do not add per-bar or per-tick level logging — orchestration events only (stage, fold, trial, unit, cache lookup).

---

## 7. Recommended TASK-3.5B Implementation Plan

### Phase A — Core stage and parallel execution (highest ROI)

**A1. Register research metrics in `observability/metrics.py`**

Add the following (follow the existing naming convention `ratp_<noun>_<verb>_<unit>`):

```
ratp_research_stage_runs_total              labels: environment, stage_name, status
ratp_research_stage_duration_seconds        labels: environment, stage_name, status
ratp_research_stage_survivors_entered       labels: environment, stage_name
ratp_research_stage_survivors_passed        labels: environment, stage_name
ratp_research_parallel_unit_runs_total      labels: environment, stage_name, mode, status
ratp_research_parallel_unit_duration_seconds labels: environment, stage_name, mode
```

**A2. Instrument `base_stage.py` or `pipeline_runner.py`**

In `PipelineRunner.run()`, wrap each `stage.run(...)` call:
- Call `record_step_started` before; `record_step_completed` / `record_step_failed` after.
- Record survivor counts into `ratp_research_stage_survivors_entered/passed`.
- Inject `LogContext(stage_name=..., n_entered=..., n_passed=..., experiment_id=..., dataset_version=...)` into existing log calls.
- Open a child OTel span `experiment_pipeline.stage` with `stage_name` attribute.

**A3. Instrument `parallel_execution_service.py`**

In `_run_serial` and `_run_parallel`:
- Log `parallel_execution_started` with `mode`, `max_workers`, `unit_count`.
- Record `ratp_research_parallel_unit_runs_total` per unit with `status=success/failed`.
- Record `ratp_research_parallel_unit_duration_seconds` per unit (wrap `unit.run()` with a timer).
- In `_run_parallel`, copy `runtime_context` into each thread worker via `contextvars.copy_context().run(...)`.

### Phase B — Checkpoint and cache visibility

**B1. Instrument `research_checkpoint_service.py`**

In each `mark_*` method:
- Emit `logger.info("checkpoint_state_transition", extra=LogContext(checkpoint_id=..., task_type=..., from_status=..., to_status=..., experiment_id=..., stage_name=...).to_extra())`.
- Add counter: `ratp_research_checkpoint_transitions_total{task_type, to_status}`.
- Add histogram: `ratp_research_checkpoint_duration_seconds{task_type}` (time from PENDING→COMPLETED).

**B2. Export cache counters from `simulation_result_cache.py`**

Replace the internal `_hits`/`_misses` integers with OTel counter instruments:
```
ratp_research_cache_lookups_total    labels: cache_type, result (hit/miss)
```
Emit at lookup and write paths. Same pattern for `strategy_generation_cache.py`.

### Phase C — Validation, regime, intelligence

**C1. Instrument `validation_orchestrator.py`**

Wrap each of the six validation stages with `record_operation_started/completed/failed`. Add:
```
ratp_research_validation_stage_runs_total       labels: stage_name, status
ratp_research_validation_stage_duration_seconds labels: stage_name
ratp_research_robustness_score                  histogram (labels: environment)
```

**C2. Instrument `regime_analysis_service.py`**

Add log events at `analyze()` entry and exit. Add:
```
ratp_research_regime_analysis_duration_seconds  labels: environment
ratp_research_regime_dimension_coverage_pct     histogram (labels: dimension)
```

**C3. Instrument `research_intelligence_service.py`**

Log ranking results (top-N scores) and cluster count. Add:
```
ratp_research_intelligence_ranking_score        histogram (labels: environment)
ratp_research_intelligence_cluster_count        histogram (labels: environment)
ratp_research_intelligence_overfit_estimate     histogram (labels: environment)
```

### Phase D — Grafana dashboard (after Phase A+B confirmed)

One dashboard JSON in `infra/observability/grafana/provisioning/dashboards/research_pipeline.json`:
- Row 1: Pipeline funnel — survivors entered vs passed per stage (bar chart).
- Row 2: Stage duration breakdown (bar chart, last N experiments).
- Row 3: Cache hit rate — simulation cache + generation cache (single stat panels).
- Row 4: Checkpoint state distribution — COMPLETED/FAILED/CACHE_HIT/SKIPPED counts.
- Row 5: Parallel execution — unit count, failure count, mode.

### Phase E — Alert rules (optional, after dashboards)

`infra/observability/prometheus-alerts-research.yaml`:
- Alert if `ratp_research_stage_survivors_passed` drops to 0 (no survivors).
- Alert if cache hit rate falls below 10% on a re-run experiment.
- Alert if experiment cycle duration exceeds SLA threshold.

---

## 8. Files to Instrument (Priority Order)

| Priority | File | Change |
|----------|------|--------|
| 1 | `observability/metrics.py` | Add research metric instruments (A1) |
| 2 | `research/pipeline/pipeline_runner.py` | Stage lifecycle instrumentation (A2) |
| 3 | `research/execution/parallel_execution_service.py` | Unit-level observability + context propagation (A3) |
| 4 | `research/checkpoints/research_checkpoint_service.py` | Checkpoint state transition events (B1) |
| 5 | `research/cache/simulation_result_cache.py` | Export hit/miss counters (B2) |
| 6 | `research/cache/strategy_generation_cache.py` | Add + export hit/miss counters (B2) |
| 7 | `research/validation/validation_orchestrator.py` | Stage-level lifecycle wrapping (C1) |
| 8 | `research/analysis/regimes/regime_analysis_service.py` | Analysis entry/exit events (C2) |
| 9 | `research/intelligence/research_intelligence_service.py` | Ranking/cluster metrics (C3) |
| 10 | `infra/observability/grafana/provisioning/dashboards/` | Research pipeline dashboard JSON (D) |

---

*See also: `docs/architecture/parallel_research_execution.md`, `docs/architecture/research_caching.md`, `docs/research_checkpoint_resume.md`*
