# Observability Instrumentation Inventory
- Last Summary Update: 5/14/2026
## Executive Summary

- Dashboard-ready: the OTLP/LGTM path is wired through `docker-compose.yml`, `infra/observability/otel-collector-config.yaml`, `prometheus-config.yaml`, and `tempo-config.yaml`; runtime cycle/job metrics, broker API metrics, reconciliation drift metrics, execution latency histograms, runtime job tables, reconciliation snapshots, runtime soak reports, and detailed health APIs all have concrete sources.
- Partially wired: several metrics are declared but have no runtime emitter found (`ratp_open_orders`, incident counter, many corporate-action throughput histograms/counters, several backfill/corporate-action distribution metrics). Runtime job steps are recorded by cycle lifecycle helpers in several cycles, but not by `RuntimeJobRunner` itself.
- Missing/stale: no alert rule files exist; metrics generally omit `run_id`, `job_run_id`, and `correlation_id` by design; Loki drilldown depends on OTLP log export and structured log fields.
- Phase 6 model: dashboards use mixed sources (Prometheus + Postgres/API + Loki + Tempo); Prometheus panels stay aggregate-only because high-cardinality IDs are intentionally not metric labels.

## Telemetry Map

| Domain | Signal Type | Name / Identifier | Source File | Emission Site | Labels / Context | Storage Backend | Dashboard Readiness | Notes |
|---|---|---|---|---|---|---|---|---|
| Observability core | Metrics | `ratp_*` instruments | `src/autonomous_trading_platform/observability/metrics.py` | Imported emitters across scheduler/execution/ingestion | Mostly `environment`, `component`, `status`, `step`, `failure_class`, `strategy_id`, broker endpoint labels | OTLP -> collector Prometheus exporter -> LGTM Prometheus | Ready/partial | All declarations central; several declared instruments have no runtime emitter found. |
| Observability core | Logs | `LogContext` fields and lifecycle events | `src/autonomous_trading_platform/observability/log_context.py`, `lifecycle.py` | `record_cycle_*`, `record_step_*`, `record_job_*`, reconciliation helpers, execution latency helper | `run_id`, `strategy_id`, `correlation_id`, `component`, `job`, `step`, `incident_type`, drift fields, broker fields | Python logging -> OTLP logs -> Loki | Ready/partial | Good drilldown fields exist, but Loki label extraction/provisioning is not defined. |
| Observability core | Traces | `start_span()` | `src/autonomous_trading_platform/observability/tracing.py` | Scheduler cycles/jobs, ingestion services/jobs, broker client, order execution service, strategy job | `ratp.span_timespan`, `ratp.correlation_id`, `ratp.run_id`, `ratp.job_name`, `ratp.strategy_id`, dataset/universe, caller attrs | OTLP -> collector -> Tempo | Ready | Error recording in helper and lifecycle failure helpers. |
| Runtime/job | DB | `runtime_job_runs` | `src/autonomous_trading_platform/runtime/services/runtime_job_runner.py`, model in `storage/sor/models/runtime_job_runs.py` | `RuntimeJobRunner.run()` | `job_name`, `parent_job_run_id`, `status`, `trigger_type`, `correlation_id`, input/output JSON | Postgres | Ready | Primary dashboard/API source for runtime health and hierarchy. |
| Runtime/job | DB | `runtime_job_run_steps` | `storage/sor/models/runtime_job_run_steps.py`, step lifecycle use in cycles | Cycle step calls in `scheduler/cycles/*` | `job_run_id`, `step_name`, `sequence_number`, status, duration, errors | Postgres | Ready/partial | Table and cycle usage exist; runner does not manage steps generically. |
| Health | API/DB | `/health`, `/api/v1/system/health`, `/api/v1/system/health/detailed` | `interfaces/rest/app.py`, `interfaces/rest/routes/system_routes.py` | FastAPI routes | status, services/checks, metadata | API backed by Postgres + TCP probes | Ready | Detailed health is front-end/API-ready Pydantic contract. |
| Reconciliation | Metrics/DB | Drift and reconciliation report signals | `execution/services/external_broker_reconciliation_service.py`, `observability/lifecycle.py` | External broker reconciliation + lifecycle helpers | `environment`, `component`, `severity`; report check type/status/severity | Prometheus + `reconciliation_snapshots` | Ready | DB snapshots carry exact drift details better than metrics. |
| Soak/replay | DB/API/CLI | `runtime_soak_reports`, `RuntimeSoakVerificationReport` | `observability/verification/runtime_soak_verification_service.py` | `operations verify-runtime-soak` | environment, checks, metadata | Postgres + CLI output | Ready | Includes metric/trace/Loki config checks and runtime integrity checks. |
| Broker/execution | Metrics/Traces/DB | Broker API and execution latency | `execution/clients/alpaca_broker_client.py`, `execution/services/order_execution_service.py`, `broker_runtime_sync_service.py` | Broker calls, retry loop, fill reconciliation, order submission | `environment`, `component`, broker, endpoint, status, optional strategy context | Prometheus + Tempo + `broker_orders`/fills | Ready | Execution timestamps persisted on broker orders. |
| Governance/allocation/risk | DB/logs | Governance jobs, audit events, risk snapshots, control state | `scheduler/cycles/run_governance_*`, `run_allocation_rebalance_cycle.py`, application services, risk snapshot job | RuntimeJobRunner, audit log repo, risk snapshot service | job names, audit event metadata, risk block fields | Postgres/API + logs | Ready/partial | No dedicated Prometheus metrics beyond risk snapshot job metrics. |
| Alerting/operator response | Prometheus rules + DB/API | `ratp-alerts.yaml`, `operational_alerts` | `infra/observability/prometheus/alerts/ratp-alerts.yaml`, `application/services/operational_alert_service.py` | Prometheus rule evaluation, operations alert APIs | alert severity/category/status plus low-cardinality environment/job/strategy context | Prometheus + Postgres + audit logs | Ready | Operator acknowledgement, snoozing, notes, and resolution are audited. |

## Metrics Inventory

All metric declarations are in `src/autonomous_trading_platform/observability/metrics.py` via meter `autonomous_trading_platform`. Type names below map directly to OpenTelemetry create calls.

### Runtime/job

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_trading_cycle_runs_total` | counter | `environment`, `component`, `status`; some early returns use `reason` | `metrics.py` | `observability/lifecycle.py`; direct updates in `scheduler/cycles/run_trading_cycle.py`; test stub in `observability/test_emit.py` | Runtime Health, Soak Verification |
| `ratp_trading_cycle_duration_seconds` | histogram, `s` | `environment`, `component`, `status`; early returns use `reason` | `metrics.py` | `record_cycle_completed/failed`; direct updates in `run_trading_cycle.py` | Runtime Health |
| `ratp_trading_cycle_step_runs_total` | counter | `environment`, `component`, `step`, `status` | `metrics.py` | `record_step_started/completed/failed` in `run_trading_cycle.py` | Runtime Health |
| `ratp_trading_cycle_step_duration_seconds` | histogram, `s` | `environment`, `component`, `step`, `status` | `metrics.py` | `record_step_completed/failed` in `run_trading_cycle.py` | Runtime Health |
| `ratp_trading_cycle_failures_total` | counter | `environment`, `component`, `failure_class` | `metrics.py` | `record_cycle_failed` in `run_trading_cycle.py` | Runtime Health, alerts |
| `ratp_trading_cycle_degraded_total` | counter | `environment`, reason/status context from caller | `metrics.py` | Direct degraded-path updates in `run_trading_cycle.py` | Runtime Health |
| `ratp_feature_pipeline_cycle_runs_total`, `ratp_feature_pipeline_cycle_failures_total`, `ratp_feature_pipeline_cycle_duration_seconds`, `ratp_feature_pipeline_cycle_step_runs_total`, `ratp_feature_pipeline_cycle_step_duration_seconds` | counters/histograms | `environment`, `component`, `status`, `failure_class`, `step` | `metrics.py` | `scheduler/cycles/run_feature_pipeline_cycle.py` via lifecycle helpers | Runtime Health |
| `ratp_experiment_pipeline_cycle_runs_total`, `ratp_experiment_pipeline_cycle_failures_total`, `ratp_experiment_pipeline_cycle_duration_seconds`, `ratp_experiment_pipeline_cycle_step_runs_total`, `ratp_experiment_pipeline_cycle_step_duration_seconds` | counters/histograms | `environment`, `component`, `status`, `failure_class`, `step` | `metrics.py` | `scheduler/cycles/run_experiment_pipeline_cycle.py` | Governance Runtime |
| `ratp_risk_snapshot_job_runs_total`, `ratp_risk_snapshot_job_failures_total`, `ratp_risk_snapshot_job_duration_seconds` | counters/histograms | `environment`, `component`, `status`/`failure_class`, optional `strategy_id` | `metrics.py` | `scheduler/jobs/run_risk_snapshot_job.py` via lifecycle helpers | Governance Runtime, Runtime Health |
| `ratp_incident_events_total` | counter | unknown | `metrics.py` | No runtime emitter found by `rg`; static/stubbed | Gaps |

### Data pipeline

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_ingestion_cycle_runs_total`, `ratp_ingestion_cycle_failures_total`, `ratp_ingestion_cycle_duration_seconds`, `ratp_ingestion_cycle_step_runs_total`, `ratp_ingestion_cycle_step_duration_seconds` | counters/histograms | `environment`, `component`, `status`, `failure_class`, `step` | `metrics.py` | `scheduler/cycles/run_market_ingestion_cycle.py` | Runtime Health |
| `ratp_ingestion_job_runs_total`, `ratp_ingestion_job_failures_total`, `ratp_ingestion_job_duration_seconds` | counters/histograms | `environment`, `component`, `status`/`failure_class`, optional `strategy_id` | `metrics.py` | `ingestion/market_data/jobs/ingest_bars_job.py` | Runtime Health |
| `ratp_ingestion_readiness_job_runs_total`, `ratp_ingestion_readiness_job_failures_total`, `ratp_ingestion_readiness_job_duration_seconds`, `ratp_ingestion_readiness_job_ingestion_lag_seconds` | counters/histograms | job lifecycle labels; lag labels from job | `metrics.py` | `scheduler/jobs/check_ingestion_readiness_job.py` | Runtime Health, Soak Verification |
| `ratp_evaluate_strategy_job_runs_total`, `ratp_evaluate_strategy_job_failures_total`, `ratp_evaluate_strategy_job_duration_seconds` | counters/histogram | `environment`, `component`, `status`/`failure_class`, optional `strategy_id` | `metrics.py` | `strategy/jobs/evaluate_strategy_job.py` via lifecycle helpers | Runtime Health |
| `ratp_trading_evaluation_job_runs_total`, `ratp_trading_evaluation_job_failures_total`, `ratp_trading_evaluation_job_duration_seconds` | counters/histogram | `environment`, `component`, `status`/`failure_class`, optional `strategy_id` | `metrics.py` | `scheduler/jobs/run_trading_evaluation_job.py` via lifecycle helpers | Runtime Health |
| `ratp_bars_ingested_total`, `ratp_missing_bars_total`, `ratp_late_bars_total`, `ratp_bar_ingestion_latency_seconds`, `ratp_ingestion_batch_size` | counters/histograms | ingestion service labels include symbol/source/runtime context where caller passes them | `metrics.py` | `ingestion/market_data/jobs/ingest_bars_job.py`, `bar_ingestion_service.py` paths; audit service also records late/missing/outlier events | Runtime Health |
| `ratp_ingestion_lag_seconds`, `ratp_symbols_expected`, `ratp_symbols_received` | observable gauges | no labels in callbacks | `metrics.py` | callback-based from `record_runtime_freshness()` in `check_ingestion_readiness_job.py` and ingestion job | Runtime Health, Soak Verification |
| `ratp_market_backfill_cycle_runs_total`, `ratp_market_backfill_cycle_failures_total`, `ratp_market_backfill_cycle_duration_seconds`, `ratp_market_backfill_cycle_step_runs_total`, `ratp_market_backfill_cycle_step_duration_seconds` | counters/histograms | cycle lifecycle labels plus `step` | `metrics.py` | `scheduler/cycles/run_market_backfill_cycle.py` | Runtime Health |
| `ratp_market_backfill_job_runs_total`, `ratp_market_backfill_job_failures_total`, `ratp_market_backfill_job_duration_seconds` | counters/histogram | job lifecycle labels | `metrics.py` | `ingestion/market_data/jobs/backfill_market_bars_job.py` | Runtime Health |
| `ratp_backfill_throughput_bars_per_second`, `ratp_historical_bars_backfilled_total`, `ratp_backfill_symbols_processed_total`, `ratp_backfill_symbol_failures_total`, `ratp_backfill_windows_processed_total`, `ratp_backfill_api_requests_total`, `ratp_backfill_api_request_failures_total`, `ratp_backfill_symbol_duration_seconds`, `ratp_backfill_window_duration_seconds`, `ratp_backfill_batch_size`, `ratp_backfill_days_requested`, `ratp_backfill_bars_per_symbol`, `ratp_backfill_request_latency_seconds` | counters/histograms | backfill service labels (`symbol`, status/source where supplied) | `metrics.py` | `ingestion/market_data/services/market_backfill_service.py`; some declared distributions need emitter confirmation | Runtime Health |
| `ratp_corporate_action_ingestion_cycle_runs_total`, `ratp_corporate_action_ingestion_cycle_failures_total`, `ratp_corporate_action_ingestion_cycle_duration_seconds`, `ratp_corporate_action_ingestion_cycle_step_runs_total`, `ratp_corporate_action_ingestion_cycle_step_duration_seconds` | counters/histograms | cycle lifecycle labels plus `step` | `metrics.py` | `scheduler/cycles/run_corporate_action_ingestion_cycle.py` | Runtime Health |
| `ratp_corporate_action_ingestion_job_runs_total`, `ratp_corporate_action_ingestion_job_failures_total`, `ratp_corporate_action_ingestion_job_duration_seconds` | counters/histogram | job lifecycle labels | `metrics.py` | `ingestion/corporate_actions/jobs/ingest_corporate_actions_job.py` | Runtime Health |
| `ratp_corporate_actions_ingested_total`, `ratp_corporate_action_records_processed_total`, `ratp_affected_symbols_processed_total`, `ratp_adjustments_applied_total`, `ratp_adjustment_failures_total`, `ratp_corporate_action_validation_failures_total`, `ratp_corporate_action_normalization_failures_total`, `ratp_unsupported_corporate_actions_total`, `ratp_corporate_action_ingestion_batch_size`, `ratp_corporate_action_request_latency_seconds`, `ratp_corporate_action_processing_duration_seconds`, `ratp_corporate_action_adjustment_duration_seconds`, `ratp_affected_bars_per_action`, `ratp_actions_per_symbol` | counters/histograms | operation-specific labels where emitted | `metrics.py` | `ingestion/corporate_actions/services/corporate_action_ingestion_service.py`; not every declared throughput/distribution metric had a direct emitter in broad search | Runtime Health, gaps |

### Reconciliation

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_order_reconciliation_job_runs_total`, `ratp_order_reconciliation_job_failures_total`, `ratp_order_reconciliation_job_duration_seconds` | counters/histogram | `environment`, `component`, `status`/`failure_class`, optional `strategy_id` | `metrics.py` | `scheduler/jobs/run_order_reconciliation_job.py` | Reconciliation |
| `ratp_order_reconciliation_mismatches_total` | counter | caller labels from order reconciliation job | `metrics.py` | `run_order_reconciliation_job.py` mismatch path | Reconciliation |
| `ratp_unreconciled_orders` | up/down counter | `environment` | `metrics.py` | `ExternalBrokerReconciliationService._emit_aggregate_metrics()` | Reconciliation, Broker Operations |
| `ratp_duplicate_fills_detected_total` | counter | `environment` | `metrics.py` | `ExternalBrokerReconciliationService._emit_aggregate_metrics()` | Reconciliation |
| `ratp_cash_drift_amount` | histogram, `USD` | `environment`, `component`, `severity` | `metrics.py` | `record_cash_drift_detected()` in `lifecycle.py` | Reconciliation |
| `ratp_position_drift_count_total` | counter | `environment`, `component`, `severity` | `metrics.py` | `record_position_drift_detected()` in `lifecycle.py` | Reconciliation |
| `ratp_position_quantity_drift` | histogram | `environment`, `component`, `severity` | `metrics.py` | `record_position_drift_detected()` | Reconciliation |
| `ratp_equity_drift_amount` | histogram, `USD` | `environment` | `metrics.py` | `ExternalBrokerReconciliationService._emit_aggregate_metrics()` | Reconciliation |

### Broker/API

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_broker_api_requests_total` | counter | `broker`, `endpoint`, method/status labels from client | `metrics.py` | `execution/clients/alpaca_broker_client.py` | Broker Operations |
| `ratp_broker_api_failures_total` | counter | `broker`, `endpoint`, `status_code` or `error` status | `metrics.py` | `alpaca_broker_client.py` exception/error paths | Broker Operations, alerts |
| `ratp_broker_api_latency_seconds` | histogram, `s` | `broker`, `endpoint`, `status_code` | `metrics.py` | `alpaca_broker_client.py` request wrapper | Broker Operations |
| `ratp_broker_api_retries_total` | counter | `broker`, `endpoint`, `exception_type` | `metrics.py` | `execution/services/order_execution_service.py` retry loop | Broker Operations |

### Execution/order lifecycle

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_order_submission_job_runs_total`, `ratp_order_submission_job_failures_total`, `ratp_order_submission_job_duration_seconds` | counters/histogram | lifecycle labels plus optional `strategy_id` | `metrics.py` | `scheduler/jobs/run_order_submission_job.py` | Broker Operations |
| `ratp_order_submission_latency_seconds` | histogram, `s` | submission labels from job | `metrics.py` | `run_order_submission_job.py` around broker submit | Broker Operations |
| `ratp_order_submission_risk_rejection_count_total` | counter | rejection labels from job, includes strategy/order context where supplied | `metrics.py` | `run_order_submission_job.py` risk-rejection path | Governance Runtime, Broker Operations |
| `ratp_order_execution_signal_to_submit_latency_seconds`, `ratp_order_execution_submit_to_ack_latency_seconds`, `ratp_order_execution_ack_to_fill_latency_seconds`, `ratp_order_execution_total_latency_seconds` | histograms, `s` | `environment`, `component`, `broker` | `metrics.py` | `record_order_execution_latency()` called by `run_order_submission_job.py` and `broker_runtime_sync_service.py` | Broker Operations |
| `ratp_open_orders` | up/down counter | unknown | `metrics.py` | Only `observability/test_emit.py` found; no production emitter found | Gap |

### Governance/allocation/risk

| Metric | Type | Labels | Source | Emitter/update site | Dashboard bucket |
|---|---|---|---|---|---|
| `ratp_risk_snapshot_job_*` | counters/histogram | lifecycle labels | `metrics.py` | `scheduler/jobs/run_risk_snapshot_job.py` | Governance Runtime |
| Governance promotion/demotion/allocation | DB/log signals | job/audit metadata | runtime job + audit tables | `run_governance_promotion_cycle.py`, `run_governance_demotion_cycle.py`, `run_allocation_rebalance_cycle.py`, `governance_automation_common.py` | Governance Runtime, Allocation Rebalance |

### Soak/replay

No dedicated `ratp_*` soak/replay metric declarations were found. Soak/replay observability is persistence-backed through `runtime_job_runs`, `runtime_soak_reports`, and CLI/runtime summaries. `RuntimeSoakVerificationService._check_metric_export()` explicitly verifies the presence of `ratp_ingestion_lag_seconds`, `ratp_symbols_expected`, `ratp_symbols_received`, and `ratp_trading_cycle_runs_total`.

### Health/control

No dedicated `ratp_*` health/control metric declarations were found. Health/control observability is API/DB/log backed through detailed health services, runtime control state, trading freeze state, audit logs, and risk snapshots.

## Logging Inventory

- Observability helper files found under `src/autonomous_trading_platform/observability`: `metrics.py`, `lifecycle.py`, `log_context.py`, `logging.py`, `tracing.py`, `runtime_context.py`, `telemetry.py`, `enums.py`, `incident_schema.py`, `utils.py`, `test_emit.py`, and `verification/runtime_soak_verification_service.py`.
- `enums.py` defines `SpanTimespan` values `instant`, `step`, `job`, `cycle`, `batch`, `request`, `backfill`, and `experiment`.
- `incident_schema.py` defines a Pydantic `Incident` model with incident type, domain, severity, component/job, run/strategy IDs, symbol/bar timestamp, dataset/universe IDs, and message. Only the metric declaration `ratp_incident_events_total` was found; no production incident emitter was found.
- `utils.py` provides `record_duration(callback)` for local duration timing.
- `test_emit.py` is a local OTLP smoke script that calls `setup_telemetry("ratp-local-test")`, emits a test span, `ratp_trading_cycle_runs_total`, `ratp_trading_cycle_duration_seconds`, and `ratp_open_orders`.
- Structured helper: `LogContext` in `src/autonomous_trading_platform/observability/log_context.py` exposes `run_id`, `strategy_id`, `symbol`, `bar_timestamp`, `cycle_timestamp`, dataset/universe IDs, `order_intent_id`, `incident_type`, `component`, `job`, `step`, `duration_seconds`, exception/error fields, `failure_class`, `correlation_id`, `parent_job_run_id`, reconciliation fields, broker fields, status code, and severity.
- Logger helper: `get_logger()` in `observability/logging.py` configures INFO stream logging and propagation.
- Lifecycle log events: `cycle_started/completed/failed`, `step_started/completed/failed`, `job_started/completed/failed`, `reconciliation_started/completed/failed`, `cash_drift_detected`, `position_drift_detected`, and `order_execution_latency_recorded` in `observability/lifecycle.py`.
- Request logging: `src/autonomous_trading_platform/api/logging_middleware.py` logs API request events; `RequestIDMiddleware` and `get_request_id` provide API request correlation.
- Audit logs: `runtime/services/audit_logging_service.py`, `storage/sor/models/audit_logs.py`, and `storage/sor/repositories/core/audit_logs_repository.py` persist run lifecycle, ingestion SLA/data events, corporate action events, operator actions, governance/allocation/control actions, failure notifications, idempotency events, and order state transitions.
- Governance/control logs: `strategy_governance_service.py`, `strategy_control_service.py`, `strategy_allocation_service.py`, `quality_based_reallocation_service.py`, `auto_promotion_service.py`, `auto_demotion_service.py`, `runtime_control_service.py`, and `operator_settings_service.py` write operator/audit events suitable for Loki/API drilldown.
- Loki drilldown readiness: good field coverage exists (`run_id`, `correlation_id`, `strategy_id`, `component`, `job`, `step`, `incident_type`), but dashboard-as-code should define Loki labels/queries explicitly because Python `extra` fields are not currently documented as Loki labels.

## Tracing Inventory

- Helper: `start_span(name, timespan=SpanTimespan, **attrs)` in `observability/tracing.py`.
- Standard span attributes: `ratp.span_timespan`, `ratp.correlation_id`, `ratp.run_id`, `ratp.job_name`, `ratp.strategy_id`, `ratp.dataset_version`, `ratp.universe_version`, plus caller-supplied attributes prefixed with `ratp.`.
- Runtime context injection: `RuntimeContext` in `observability/runtime_context.py` is a `ContextVar`; `RuntimeJobRunner.run()` binds `job_run_id`, `job_name`, and `correlation_id`; scheduler cycles bind run/strategy/environment context directly.
- Important traced paths from `rg start_span`: trading, market ingestion, market backfill, feature pipeline, experiment pipeline, corporate action ingestion cycles; `check_ingestion_readiness_job`, `run_order_reconciliation_job`, `run_order_submission_job`, `run_risk_snapshot_job`, `run_trading_evaluation_job`, `evaluate_strategy_job`; ingestion services; `AlpacaBrokerClient`; `OrderExecutionService`.
- Broker/API spans: `alpaca_broker_client.py` wraps broker requests; `order_execution_service.py` wraps submit/retry behavior and records exceptions/retry attributes.
- Error behavior: `start_span.__exit__()` records exceptions and sets error status; lifecycle failure helpers also call `trace.get_current_span().record_exception()` and set `StatusCode.ERROR`.
- Trace link readiness: Tempo is configured, and spans include `ratp.correlation_id`/`ratp.run_id` when runtime context is bound. Metrics do not include correlation labels, so trace links should pivot from DB/API rows or logs rather than Prometheus series.

## Persistence-backed Observability Inventory

| Table/report | Model / Contract | Writer | Key fields | Dashboard use |
|---|---|---|---|---|
| `runtime_job_runs` | `storage/sor/models/runtime_job_runs.py`, `contracts/runtime/runtime_job_run.py` | `RuntimeJobRunner.run()`, direct cycle code in older paths | `job_run_id`, `job_name`, `parent_job_run_id`, `status`, `trigger_type`, `started_at`, `completed_at`, `duration_ms`, `error_message`, `correlation_id`, input/output JSON | Runtime Health, Governance Runtime, Allocation Rebalance, Soak Verification |
| `runtime_job_run_steps` | `storage/sor/models/runtime_job_run_steps.py` | Cycle step lifecycle usage in `scheduler/cycles/*` | `step_id`, `job_run_id`, `step_name`, `status`, `sequence_number`, timings, error fields | Runtime Health step panels |
| `reconciliation_snapshots` | `contracts/runtime/reconciliation_report.py`, `storage/sor/models/reconciliation_snapshots.py` | `ReconciliationSnapshotRepository.append_report()` | `run_id`, `reconciled_at`, `check_type`, `symbol`, expected/actual/delta/tolerance, `severity`, `status`, `detail` | Reconciliation Dashboard |
| `runtime_soak_reports` | `contracts/runtime/runtime_soak_verification.py`, `storage/sor/models/runtime_soak_reports.py` | `RuntimeSoakReportRepository.append_report()` from `RuntimeSoakVerificationService.verify()` | `status`, `environment`, `checked_at`, window, `failed_checks`, `runtime_metadata`, full `report_json` | Soak Verification Dashboard |
| `operational_alerts` | `storage/sor/models/operational_alerts.py` | `OperationalAlertService` through operations APIs | alert identity/fingerprint, category, severity, status, environment, job/strategy context, snooze/ack/resolve fields, notes | Alert acknowledgement, snoozing, incident response |
| `broker_orders` | `storage/sor/models/broker_orders.py`, migration `jj67kk89ll01_add_execution_timestamps_to_broker_orders.py` | order submission, broker runtime sync, reconciliation | broker/client/order IDs, status, symbol, qty/price, timestamps including `signal_generated_at`, `submitted_to_broker_at`, `broker_acknowledged_at`, `first_fill_at` | Broker Operations latency/order lifecycle |
| `risk_snapshots` | `storage/sor/models/risk_snapshots.py` | `scheduler/jobs/run_risk_snapshot_job.py`, replay/backtest services | snapshot timestamp, exposure/risk fields, `is_blocked`, `block_reasons` | Governance Runtime, Runtime Health |
| `run_manifests` | `storage/sor/models/run_manifests.py` | cycle runners and governance helpers | `run_id`, run type/interval/environment/strategy, status/current step/last successful step/error, dataset/artifact manifests | Runtime Health, Soak Verification |
| `audit_logs` | `storage/sor/models/audit_logs.py` | `AuditLoggingService`, operator/control/governance/allocation services | event type/timestamp/run ID/message/metadata | Loki/API drilldowns, Governance Runtime |
| `fill_quality_metrics` | `storage/sor/models/fill_quality_metrics.py` | `RealisedSlippageService` | strategy/symbol/run, expected/actual price, slippage/latency fields | Broker Operations fill quality panels |
| `broker_account_snapshots`, `cash_snapshots`, `position_snapshots` | storage models/repositories | `BrokerRuntimeSyncService`, backtest/replay | broker/cash/equity/position state over time | Reconciliation, Broker Operations |

## Health Endpoint Inventory

- `GET /health` in `interfaces/rest/app.py`: unauthenticated liveness, returns `{"status": "ok"}`.
- `GET /api/v1/system/health` in `interfaces/rest/routes/system_routes.py`: auth-required lightweight health with status, trading mode, active strategy count, alerts. Backed by `SystemHealthService`.
- `GET /api/v1/system/health/detailed`: auth-required detailed report from `DetailedSystemHealthService`; aggregates:
- `OtelHealthService`: `otel_collector_reachable`, `otel_metric_exporter`, `otel_trace_exporter`, `otel_log_exporter`.
- `JobHealthService`: `job_stale`, `job_hung`, `job_orphaned`, `job_duplicate_running`, `job_dead`.
- `DataPipelineHealthService`: `data_raw_bars_freshness`, `data_feature_freshness`, `data_manifest_freshness`, `data_ingestion_lag`, `data_feature_lag`.
- `BrokerHealthService`: `broker_connectivity`, `broker_auth`, `broker_order_endpoint`, `broker_reconciliation_freshness`.
- `ControlStateHealthService`: `control_kill_switch`, `control_pause_state`, `control_freeze_state`, `control_degradation_state`.
- Tests: `tests/interfaces/rest/test_system_routes.py`, `tests/interfaces/rest/test_dashboard_api_real_runtime_state.py`, plus health service tests surfaced under `tests/application/services`.

## CLI/Runner Observability Entry Points

- `operations verify-runtime-soak` in `src/autonomous_trading_platform/cli/commands/operations.py`: runs `RuntimeSoakVerificationService`, prints report, persists `runtime_soak_reports`.
- `runtime soak-loop paper|research|backtest` in `src/autonomous_trading_platform/cli/commands/runtime.py` and `runtime_soak_loop.py`: long-running soak harnesses; rescue stale orphan jobs at startup through `OrphanJobRecoveryService`.
- `runtime replay-debug` in `runtime/replay_debug.py`: creates runtime replay job evidence with job names such as `runtime_replay_debug.trading` and `runtime_replay_debug.runtime_checks`; refuses live trading.
- `execution reconcile-order` and `execution reconcile-open-orders` in `cli/commands/execution.py`: manual broker reconciliation entry points.
- `runtime inspect-manifest` and `runtime inspect-audit`: DB-backed run/audit inspection entry points documented and registered under runtime CLI.
- Scheduler registry job names: `market_ingestion_cycle`, `feature_pipeline_cycle`, `trading_cycle`, `strategy_allocation_rebalance_cycle`, `strategy_auto_promotion_cycle`, `strategy_auto_demotion_cycle`, `corporate_action_ingestion_cycle`, `experiment_pipeline_cycle`.
- Orchestrator job names: `paper_trading_intraday_tick`, `paper_trading_golden_path`, `paper_trading_eod_maintenance`, `historical_research_golden_path`, `historical_ingestion_replay`.

## Current LGTM/Grafana/OTel Configuration

- `docker-compose.yml` services:
- `lgtm`: image `grafana/otel-lgtm:latest`, container `ratp_lgtm`, exposes Grafana `3000:3000`, admin/admin, mounts `tempo-config.yaml` and `prometheus-config.yaml`, persists `ratp_lgtm_data`.
- `otel-collector`: image `otel/opentelemetry-collector-contrib:latest`, container `ratp_otel_collector`, exposes `4317`, `4318`, `9464`, mounts `otel-collector-config.yaml`.
- `infra/observability/otel-collector-config.yaml`: OTLP gRPC/HTTP receiver; memory limiter, 100% probabilistic sampler, batch; exporters `debug`, `otlphttp/tempo` to `http://lgtm:4318`, `otlp_http/loki` to `http://lgtm:3100/otlp`, and Prometheus exporter on `0.0.0.0:9464`; traces/metrics/logs pipelines are present.
- `infra/observability/prometheus-config.yaml`: scrape interval `5s`, scrape job `otel-collector`, target `otel-collector:9464`.
- `infra/observability/tempo-config.yaml`: Tempo HTTP `3200`, gRPC `9096`, OTLP receiver on `4317/4318`, local storage with 168h retention.
- `infra/observability/prometheus.yaml`: exists but was not readable in this sandbox session due permission denial; `prometheus-config.yaml` is the compose-mounted file.
- Grafana provisioning: datasource, dashboard provider, and dashboard JSON files live under `infra/observability/grafana/` and are mounted into the LGTM container by `docker-compose.yml`.
