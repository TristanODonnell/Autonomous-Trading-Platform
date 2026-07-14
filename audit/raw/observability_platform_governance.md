# Audit: observability/ + platform/ + governance/

## Verified counts

Command (Git Bash, repo root):
```
for d in observability platform governance; do find src/autonomous_trading_platform/$d -type f -name '*.py' | sort | xargs wc -l; done
```
Output (totals):
- `src/autonomous_trading_platform/observability/` — **16 files, 4,164 LOC** (largest: metrics.py 1,932; verification/runtime_soak_verification_service.py 1,122; lifecycle.py 646)
- `src/autonomous_trading_platform/platform/` — **3 files, 772 LOC** (all in replay/platform_replay_config.py; both `__init__.py` empty)
- `src/autonomous_trading_platform/governance/` — **13 files, 806 LOC**

TODO/FIXME/XXX:
```
grep -rnE 'TODO|FIXME|XXX' src/autonomous_trading_platform/{observability,platform,governance} | wc -l
2
src/autonomous_trading_platform/governance/deployment/audit_logger.py:12:TODO(TASK-188): implement a real logger that persists deployment events
src/autonomous_trading_platform/governance/deployment/role_checker.py:58:    TODO(TASK-189): implement real role lookup against the RBAC store.
```

## Claim verification

**Claim 1 — custom span helper auto-injecting run-scoped context.** VERIFIED, with one nuance. `observability/tracing.py` defines `start_span(name, *, timespan, **attrs)` returning `_SpanWithTimespan`, whose `__enter__` reads the `ContextVar`-backed `RuntimeContext` (`observability/runtime_context.py`) and stamps `ratp.correlation_id`, `ratp.run_id`, `ratp.environment`, `ratp.job_run_id`, `ratp.job_name`, `ratp.strategy_id`, `ratp.dataset_version`, `ratp.universe_version` onto the span, plus a mandatory `ratp.span_timespan` duration-taxonomy attribute (`enums.SpanTimespan`: instant/step/job/cycle/batch/request/backfill/experiment). `__exit__` records exceptions and sets ERROR status. Nuance: injection applies to spans opened via this helper, not literally "every trace" — code that calls `tracer.start_as_current_span` directly (e.g. `test_emit.py`) bypasses it. Safe phrasing: "a span helper that auto-injects run-scoped context onto every span opened through it."

**Claim 2 — "1,932-line metrics module."** VERIFIED exactly: `observability/metrics.py` is **1,932 lines** (`wc -l`). Verified instrument counts (grep on `meter.create_*`): **126 counters, 129 histograms, 2 up-down counters, 4 observable gauges = 261 OTel instruments**, all prefixed `ratp_`. The observable-gauge-callback pattern is real but limited to 4 gauges (`ratp_ingestion_lag_seconds`, `ratp_symbols_expected`, `ratp_symbols_received`, `ratp_universe_active_size`) whose callbacks read module-level state set by `record_runtime_freshness()` / `record_universe_active_size()`. Cycle/order/reconciliation telemetry is covered by counters/histograms (trading/ingestion/backfill/corporate-action/feature/experiment/governance/allocation cycles; order submission and signal→submit→ack→fill latency ladder; broker-drift reconciliation histograms in USD).

**Claim 3 — platform/ contents (fresh characterization).** `platform/` is NOT a platform layer — it is a single 772-line module, `platform/replay/platform_replay_config.py`: the YAML fixture parser/validator for the "platform replay" harness. Pydantic fixture schema (replay block, initial state, scheduled jobs with cadence validation, timeline events across controls/settings/governance/safety domains, failure injections), CLI>fixture>default merge (`merge_fixture_with_cli`), a `validate_plan` dry-run planner that import-checks 14 domain hook modules under `application/services/platform_replay/`, and `dispatch_failure_injection` routing 9 failure kinds (missing/late bars, feature validation failure, risk-limit breach, drawdown breach, governance demotion trigger, broker reconciliation mismatch, order rejected, runtime job failure) to injection hooks. The actual replay runner/hooks live in the application layer, not here.

**Claim 4 — governance/ contents (fresh characterization).** `governance/` is the strategy *deployment* governance package (distinct from the governance metrics/services in `application/`): a 6-state `GovernanceState` FSM (PROPOSED → APPROVED_RESEARCH → APPROVED_PAPER → APPROVED_LIVE, plus REJECTED with re-submission and terminal RETIRED) with an explicit `ALLOWED_TRANSITIONS` table; a stateless `DeploymentGate` (paper requires APPROVED_PAPER, live requires APPROVED_LIVE); an **in-memory** `DeploymentRegistry` (deploy/pause/stop/rollback with per-key history); Protocol-based DI seams for RBAC and audit logging — both currently placeholders: `StubDeploymentRoleChecker` always permits (emits a `warnings.warn` on construction) and `NoOpDeploymentAuditLogger` does nothing (the only 2 TODOs in scope, TASK-188/189).

---

## Per-file entries

### src/autonomous_trading_platform/observability/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/observability/correlation_links.py (17 lines)
- Purpose: builds Grafana Explore deep-link URLs (Tempo trace query and Loki log query) from a correlation_id.
- Notable: hand-assembled JSON-in-URL strings; both query on `correlation_id`, matching the span attribute `ratp.correlation_id`.

### src/autonomous_trading_platform/observability/enums.py (19 lines)
- Purpose: `SpanTimespan` StrEnum — a duration taxonomy (instant/step/job/cycle/batch/request/backfill/experiment) stamped on every span opened via `start_span`.

### src/autonomous_trading_platform/observability/incident_schema.py (29 lines)
- Purpose: Pydantic `Incident` model for incident events (type, domain, severity, component, run/strategy ids, symbol, dataset/universe versions).
- Notable: `domain` is `Literal["ingestion", "scheduler"]` only — narrower than the platform's actual incident surface.

### src/autonomous_trading_platform/observability/lifecycle.py (646 lines)
- Purpose: uniform lifecycle telemetry helpers — `record_cycle_/step_/job_{started,completed,failed}`, generic operation events, reconciliation start/complete/fail, cash/position drift detection, and the 4-phase order execution latency ladder. Each helper pairs a structured log (via `LogContext.to_extra()`) with metric add/record and, on failure, `span.record_exception` + ERROR status.
- Notable: `StepMetricSet`/`JobMetricSet`/`CycleMetricSet` frozen dataclasses group (runs, failures, duration) instruments so ~9 cycle families reuse one code path; correlation_id and strategy_id are pulled from RuntimeContext automatically; metric fields typed `Any` and `logger` params untyped — loose typing in an otherwise disciplined module. `failure_class` label supports failure taxonomy on counters.

### src/autonomous_trading_platform/observability/log_context.py (64 lines)
- Purpose: frozen dataclass of ~60 optional structured-logging fields; `to_extra()` emits only non-None fields as `logging` extras.
- Notable: single grab-bag context type spanning ingestion, reconciliation, governance, research; keeps field names consistent across the codebase but is a god-object by design.

### src/autonomous_trading_platform/observability/logging.py (16 lines)
- Purpose: `get_logger` — stdlib logger factory adding one StreamHandler with a fixed format.
- Notable: sets both a direct handler and `propagate = True`; with the OTel root `LoggingHandler` installed by `telemetry.py` this is fine, but with any root StreamHandler it would double-print. Hardcodes INFO level.

### src/autonomous_trading_platform/observability/metric_labels.py (48 lines)
- Purpose: canonical metric label builders — `component_labels` (environment+component) and `broker_labels` (adds broker/endpoint/status/strategy_id).
- Notable: environment resolves RuntimeContext first, then `RATP_ENVIRONMENT` env var captured at import time.

### src/autonomous_trading_platform/observability/metrics.py (1,932 lines)
- Purpose: the platform's entire OTel metric registry — 261 instruments (126 counters, 129 histograms, 2 up-down counters, 4 observable gauges), all `ratp_`-prefixed, organized in commented sections per domain: trading cycle, order submission/execution latency ladder, broker API, order + external broker reconciliation (drift histograms in USD), sector concentration, ingestion, market backfill, corporate actions, feature/experiment pipelines, governance/allocation/rebalance, portfolio drawdown governance, universe lifecycle, signal aggregation, research (stages, parallel units, checkpoints, cache, validation, regime, intelligence), position scaling, strategy health + health lifecycle, correlation/covariance, risk parity, MVO, factor exposure/neutralization, optimizer backend, shadow validation, metric lineage, portfolio construction, drawdown ladder, governance audit completeness.
- Notable: mostly declarative instrument definitions plus `record_universe_*` helper functions that normalize inputs (`max(0, …)`) and build labels from RuntimeContext; observable gauges read module-level mutable state (`RuntimeFreshnessState`, `_universe_active_size_state`) — process-local, so gauges reflect only the last writer in this process. Comments trace instruments to task/finding IDs (TASK-514..519, FINDING-08/09/12/13/16/18, REC 6.x) — strong requirements traceability. Small smells: alias `universe_rejected_trade_outside_universe_count = universe_rejected_trade_count`; variable `strategy_health_lifecycle_suspended_total` maps to metric name `ratp_strategy_suspended_total` (name/variable mismatch).

### src/autonomous_trading_platform/observability/runtime_context.py (67 lines)
- Purpose: `RuntimeContext` frozen dataclass (correlation_id, run_id, strategy_id, dataset_version, universe_version, job_name, job_run_id, environment) carried in a `ContextVar`; `runtime_context(**kwargs)` context manager layers/merges nested contexts and restores on exit.
- Notable: async-safe propagation via ContextVar tokens; nested binds merge non-None fields over the parent; unknown kwargs silently ignored (typo-tolerant, arguably too forgiving); all values coerced to `str`.

### src/autonomous_trading_platform/observability/telemetry.py (62 lines)
- Purpose: one-call OTel bootstrap — OTLP gRPC exporters for traces + metrics (5s periodic reader), OTLP HTTP for logs, shared `Resource` (service.name/namespace, deployment.environment from APP_ENV), root-logger `LoggingHandler` for Loki-bound logs.
- Notable: idempotent handler install; endpoints from `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` with localhost defaults matching docker-compose (4317/4318); `insecure=True` hardcoded (local-dev assumption).

### src/autonomous_trading_platform/observability/test_emit.py (61 lines)
- Purpose: manual smoke script (`__main__`) emitting 10 batches of spans/metrics/logs to verify the LGTM stack end-to-end.
- Notable: not a pytest test despite the `test_` prefix — misleading name inside `src/`; uses raw `tracer.start_as_current_span` (so no auto-injected context, consistent with the helper being opt-in).

### src/autonomous_trading_platform/observability/tracing.py (61 lines)
- Purpose: the custom span helper (see Claim 1). `start_span` wraps `tracer.start_as_current_span` in `_SpanWithTimespan`, which stamps the timespan taxonomy, auto-injects 8 RuntimeContext fields as `ratp.*` attributes, namespaces extra kwargs, and records exception + ERROR status on exit.
- Notable: special-cases `broker_endpoint`/`broker_status` kwargs into dotted `ratp.broker.*` attributes; `__exit__` uses `trace.get_current_span()` rather than the span captured in `__enter__` (equivalent while current, but indirect).

### src/autonomous_trading_platform/observability/utils.py (14 lines)
- Purpose: `record_duration` context manager — perf_counter timing delivered to a callback in `finally` (records duration even on exception).

### src/autonomous_trading_platform/observability/verification/__init__.py (6 lines)
- Purpose: re-exports `RuntimeSoakVerificationService` and `EXPECTED_RUNTIME_JOBS`.

### src/autonomous_trading_platform/observability/verification/runtime_soak_verification_service.py (1,122 lines)
- Purpose: automated soak-test verifier: runs **14 checks** over a time window against the SoR (via injected repository) and produces a persisted `RuntimeSoakVerificationReport` with PASSED/WARNING/FAILED per check plus rollup. Checks: runtime job health (expected jobs `market_ingestion_cycle`/`feature_pipeline_cycle`/`trading_cycle`, slow-job and recovered-failure detection), data freshness (bars/features/manifest lag vs 15-min threshold), stale RUNNING state, concurrent RUNNING jobs (no-overlap lock verification), order reconciliation invariants, duplicate-fill protection (3 independent detectors: broker_fill_id, order+execution_id, idempotency key), cash/position/equity drift vs broker snapshots with Decimal tolerances ($1 cash, $5 equity, 1e-6 qty), metric export, trace export, Loki ingestion, governance runtime coverage, replay runtime evidence, long-running integrity, and failure-controls (a failed job must leave failed manifest + audit event + kill-switch/risk-block evidence).
- Notable: severity escalation to CRITICAL for stale state, concurrency violations, and confirmed duplicate fills; rich structured metadata per check (serialized orders, drift values). Weakness: the metric/trace/Loki checks verify by reading `infra/observability/otel-collector-config.yaml` from disk (`Path(__file__).parents[4]`) and substring-matching tokens like `"receivers: [otlp]"` vs `"receivers: [ otlp ]"` — whitespace-brittle config presence checking, not live telemetry verification. `_check_observability_signals` is dead code (delegates to `_check_metric_export`, never called). The compound drift condition at L637-643 relies on `and`/`or` precedence without parentheses (correct but fragile).

### src/autonomous_trading_platform/platform/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/platform/replay/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/platform/replay/platform_replay_config.py (772 lines)
- Purpose: YAML fixture parser/validator for the platform replay harness (see Claim 3): Pydantic fixture schema with frozenset-validated vocabularies (11 scheduled job names, 7 cadences, 21 timeline event types across controls/settings/governance/safety, 6 failure targets × 9 failure kinds), CLI>fixture>default parameter merge, typed-event builder mapping fixture events to runtime contract dataclasses, `validate_plan` dry-run (trading-day count, event date-bounds warnings, import-availability probe of 14 domain hook modules, declared `intended_writes` manifest), and `dispatch_failure_injection` routing failures to application-layer injection hooks with session rollback on error.
- Notable: deliberate failure-injection design (chaos-testing the replay); YAML bare-date coercion handled in validators; documented CLI-precedence contract in module docstring; `mode` restricted to `"historical_backtest"`. Smells: `rotation_days` list comp includes a redundant `weekday() < 5` after `weekday() == 0`; `drawdown_breach`/`governance_demotion_trigger` injections pass `symbols[0]` as `strategy_id` (symbol/strategy conflation, presumably a replay convention); imports deferred inside functions to avoid import cycles.

### src/autonomous_trading_platform/governance/__init__.py (0 lines)
- Purpose: empty package marker. (Also empty: `exceptions/__init__.py`, `models/__init__.py`, `services/__init__.py`.)

### src/autonomous_trading_platform/governance/deployment/__init__.py (41 lines)
- Purpose: public API surface — re-exports registry, gate, exceptions, models, role checker, audit logger with `__all__`.

### src/autonomous_trading_platform/governance/deployment/audit_logger.py (83 lines)
- Purpose: `DeploymentAuditLogger` runtime-checkable Protocol (log_deployed/paused/stopped/rollback/environment_switch) plus `NoOpDeploymentAuditLogger` placeholder.
- Notable: honest deferral — TODO(TASK-188) documents the intended Postgres-persisted implementation; DI seam means swapping in a real logger needs zero caller changes. Currently no deployment action is durably audited.

### src/autonomous_trading_platform/governance/deployment/deployment_gate.py (75 lines)
- Purpose: stateless gate enforcing paper→APPROVED_PAPER / live→APPROVED_LIVE before deploy; raising (`assert_deployable`) and boolean (`is_deployable`) forms.
- Notable: exact-state match (not ≥ ordering) — an APPROVED_LIVE strategy cannot deploy to paper, which may be intentional but is a real behavioral constraint; `_REQUIRED_STATE[environment]` would KeyError on a new environment enum value rather than fail with a domain error.

### src/autonomous_trading_platform/governance/deployment/deployment_registry.py (349 lines)
- Purpose: central deployment registry — deploy (gate + RBAC checked), pause, stop, rollback (restores previous record from per-key history with a fresh deployment_id and `rolled_back_from_deployment_id` pointer), query helpers, and `assert_strategy_is_deployed_and_active` guard for the execution engine (raises if PAUSED).
- Notable: **in-memory only** — `_store` dict keyed by (strategy_id, environment); docstring explicitly flags the Postgres-repository swap as follow-up work, so deployments do not survive process restart. Immutable `DeploymentRecord` mutations via `model_copy`; rollback docstring honestly warns the restored governance state may be stale. Clean checks-in-order design (gate → RBAC → mutate → audit).

### src/autonomous_trading_platform/governance/deployment/role_checker.py (77 lines)
- Purpose: `DeploymentAction` enum (deploy/pause/stop/rollback), `DeploymentRoleChecker` Protocol, and `StubDeploymentRoleChecker` that always returns True.
- Notable: stub is "intentionally loud" — emits `warnings.warn` at construction so it cannot silently reach production; RBAC is entirely unimplemented (TODO TASK-189).

### src/autonomous_trading_platform/governance/exceptions/deployment_exceptions.py (49 lines)
- Purpose: `DeploymentGateError` (carries strategy/env/current/required state), `DeploymentPermissionError`, `DeploymentNotFoundError` — structured exceptions with self-describing messages.

### src/autonomous_trading_platform/governance/models/deployment_models.py (38 lines)
- Purpose: `DeploymentEnvironment` (paper/live), `DeploymentStatus` (active/paused/stopped) StrEnums, and immutable Pydantic `DeploymentRecord` snapshot including `governance_state_at_deploy` and rollback pointer.

### src/autonomous_trading_platform/governance/models/governance_state.py (43 lines)
- Purpose: the 6-state `GovernanceState` StrEnum and `ALLOWED_TRANSITIONS` adjacency table + `is_valid_transition` (see Claim 4).
- Notable: research→paper→live promotion pipeline with REJECTED→PROPOSED re-submission loop and terminal RETIRED; declarative table makes the FSM auditable at a glance.

### src/autonomous_trading_platform/governance/services/governance_state_machine.py (51 lines)
- Purpose: `GovernanceStateMachine.transition` — validates against the transition table, raises `InvalidGovernanceTransitionError`, returns an updated immutable `StrategyGovernanceRecord` copy; `propose` factory creates PROPOSED records bound to config_hash/experiment_id/source_run_id.
- Notable: `actor` parameter is accepted but unused in the record update (no actor stamped on transition — audit gap consistent with the deferred audit logger); `source_run_id` untyped.

---

## (a) Standout candidates

- **`observability/metrics.py` (1,932 lines, 261 OTel instruments)** — verified line count; domain-sectioned metric registry with task/finding-ID traceability (TASK-514..519, FINDING-xx, REC 6.x) and 4 observable-gauge callbacks over process state.
- **`observability/tracing.py` + `observability/runtime_context.py`** — ContextVar-propagated run-scoped context (correlation_id, run_id, strategy_id, dataset_version, universe_version, job ids, environment) auto-stamped as `ratp.*` attributes on every span opened via `start_span`, plus a span duration taxonomy enum; pairs with `correlation_links.py` Grafana Tempo/Loki deep links for one-click correlation.
- **`observability/verification/runtime_soak_verification_service.py` (1,122 lines)** — 14-check automated soak verifier (job health, freshness, stale/concurrent state, order reconciliation, triple-detector duplicate-fill protection, Decimal-tolerance cash/position/equity drift vs broker, governance/replay coverage, failure-control forensics) with severity escalation and persisted reports.
- **`observability/lifecycle.py` (646 lines)** — single vocabulary for cycle/step/job/reconciliation lifecycle events pairing structured logs, metrics, and span error status; MetricSet dataclasses let ~9 cycle families share one implementation.
- **`platform/replay/platform_replay_config.py` (772 lines)** — fixture-driven whole-platform replay with a validated failure-injection vocabulary (9 failure kinds × 6 targets) and a dry-run `validate_plan` that probes 14 domain hook modules — deliberate chaos-testing infrastructure.
- **`governance/` deployment package** — explicit 6-state promotion FSM with declarative transition table, stateless deployment gate, and Protocol-based DI seams for RBAC/audit.

## (b) Gaps/smells

- **Governance deployment is prototype-grade in three explicit ways**: registry is in-memory only (lost on restart; Postgres swap documented as follow-up), RBAC is a stub that always permits (TASK-189), audit logging is a no-op (TASK-188). All honestly flagged in-code, but a writeup must not claim persisted/enforced deployment governance.
- Soak service's metric/trace/Loki "export" checks are whitespace-sensitive substring matches against the collector YAML on disk — config-presence checking, not live telemetry verification; `Path(__file__).parents[4]` repo-root resolution breaks under installed-package layouts. `_check_observability_signals` is dead code.
- `GovernanceStateMachine.transition` accepts `actor` but never records it — no actor attribution on transitions.
- `platform/` dir name oversells: 2 of 3 files are empty `__init__.py`s; the whole package is one config module (the replay runner lives in application/).
- `log_context.LogContext` is a ~60-field god dataclass; `incident_schema.Incident.domain` limited to ingestion|scheduler.
- Minor: `test_emit.py` smoke script named like a pytest test inside src/; metrics alias `universe_rejected_trade_outside_universe_count`; variable/metric-name mismatch (`strategy_health_lifecycle_suspended_total` → `ratp_strategy_suspended_total`); redundant weekday condition in replay `validate_plan`; unparenthesized and/or chain in soak drift check; failure injections conflate `symbols[0]` with `strategy_id`.
- Observable gauges read module-level mutable state — correct for single-process runtimes, silently last-writer-wins if multiple components set freshness in one process.

## (c) Coverage

- observability/: read **16 of 16** files (incl. both `__init__.py`s). None skipped.
- platform/: read **3 of 3** files. None skipped.
- governance/: read **13 of 13** files (incl. 5 `__init__.py`s, 4 of them empty). None skipped.
