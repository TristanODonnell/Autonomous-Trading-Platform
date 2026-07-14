# Audit: application/services (basenames ^[_a-l]) — files a-l

Scope enumeration command:
```
find src/autonomous_trading_platform/application/services -name '*.py' | sort | grep -E '/[_a-l][^/]*$'
```
Verified counts (commands run 2026-07-07):
- **File count:** 33 (`... | wc -l` → `33`)
- **LOC:** 10,233 (`... | xargs wc -l | tail -1` → `10233 total`)
- **Class count:** 39 (`grep -c '^class ' <files> | awk` sum → `classes: 39`)
- **TODO/FIXME/XXX:** 0 (`grep -nE 'TODO|FIXME|XXX' <files> | wc -l` → `0`)

Note: this scope includes the `health/` and `platform_replay/` subpackage files whose basenames match `^[_a-l]` (e.g. `platform_replay/admin_hooks.py`); the m-z sibling covers the rest.

---

### src/autonomous_trading_platform/application/services/auto_demotion_service.py (1003 lines)
- Purpose: Automatic governance demotion. Scans strategies in live/paper approval states, compares latest performance observations (MetricsSummary or RiskSnapshot) against per-transition maintenance rules (max drawdown, min Sharpe, min win rate), and demotes breaching strategies one rung down the ladder (live → paper → research).
- Notable:
  - Real state machine: `_DEMOTABLE_STATES` / `_NEXT_STATE` / `_MAINTENANCE_RULE_STATUS` maps at top of file define the ladder; delegates the actual transition to `StrategyGovernanceService.transition()`.
  - Defense-in-depth on breach: disables strategy via control-state repo, zeroes allocation via a 0% `AllocationOverrides` row, and if drawdown ≥ 2× the maintenance threshold triggers a global `TradingFreezeService.freeze_trading()` (kill-switch escalation).
  - Idempotency: `breach_key = strategy:source_type:source_id:metric:threshold`; `_breach_already_demoted()` queries the audit log so the same breach never demotes twice.
  - Sample-size guard (`_sample_skip_reason`): skips demotion when trade_count/bars are below lookback windows — avoids demoting on noise.
  - Full audit trail: every run (including disabled/dry-run) writes `STRATEGY_AUTO_DEMOTION_{COMPLETED,SKIPPED}` audit rows plus per-candidate governance-audit evidence and optional notification/drawdown-alert events. OTel metrics (`auto_demotion_scan_total`, `..._breach_total`, scan duration histogram).
  - Supports `dry_run` and `enforce_enabled` (respects operator setting `auto_demote_on_breach`).
  - **Refutes strict "no direct ORM" claim**: mixes repositories with direct `session.scalars(select(...))` for StrategyGovernance/MetricsSummary/RiskSnapshot and `session.query(AuditLogRow)` for breach dedup; also calls `session.flush()/commit()/rollback()` directly (plain `Session`, not a UnitOfWork wrapper).
  - Exception handling: `except Exception` around each transition — but not swallowed: rolls back, logs with `exc_info`, records a `status: failed` row in the run result. Reasonable per-candidate isolation.
  - Smell: `_latest_metrics_for_strategy` / `_latest_risk_for_strategy` load ALL MetricsSummary/RiskSnapshot rows and filter in Python by `metrics_json["strategy_id"]` (JSON column not filterable portably in SQLite tests) — O(table size) per strategy per scan.
  - Smell: `_latest_governance()` (last method) appears unused within the file.

### src/autonomous_trading_platform/application/services/auto_promotion_service.py (774 lines)
- Purpose: Automatic governance promotion. Evaluates strategies in research/paper states against active `PromotionRules` (min sharpe / max drawdown / min days tested / min trade count / min CAGR / min win rate) and promotes eligible ones (research → paper → live), with evidence-complete audit for both promotions and rejections.
- Notable:
  - **Fail-closed for capital-bearing transitions**: `_CAPITAL_BEARING_AUTO_PROMOTION_TRANSITIONS` (research→paper, paper→live) require an explicit `source_run_id` on the governance record; without it the candidate is skipped with a `PROMOTION_MISSING_SOURCE_RUN` audit event and no metric fallback is allowed. Non-capital paths may fall back to latest MetricsSummary.
  - Config-vs-strategy failure separation: a null *required* threshold on a rule is classified `invalid_rule_config` (emits `PROMOTION_RULES_CONFIGURATION_ERROR`), not a strategy ineligibility — imports `_REQUIRED_CRITERIA_BY_TRANSITION` / `_RULE_STATE_ALIASES` from StrategyGovernanceService (private-name import across modules, minor smell).
  - Live runtime metrics from `LivePerformanceMetricsService` are attached to every eligibility result but explicitly documented as "advisory only" — promotion decided solely on backtest/simulation metrics.
  - Rejected candidates get `record_promotion_decision(eligible=False, criteria=...)` in the governance audit — negative evidence is persisted, not just successes.
  - Same layering deviations as demotion: direct `session.scalars/execute/get` for governance rows and metrics join, direct `commit()/rollback()`; `except Exception` around transition is captured into a `status: failed` payload (not silently swallowed) but does NOT log the exception (unlike demotion service).
  - Same O(N)-scan smell in `_latest_metrics_from_json` (loads all MetricsSummary rows, filters JSON in Python).
  - Deprecated operator-settings thresholds (`min_sharpe_for_promotion`, `min_paper_trading_period_days`) are deliberately surfaced as ignored in audit payloads — nice migration breadcrumb.

### src/autonomous_trading_platform/application/services/drawdown_governance_service.py (900 lines)
- Purpose: Per-strategy drawdown governance ladder (normal → warning → probation → suspended → breached) driven by drawdown utilization (realized_drawdown / max_drawdown_allowed). Produces an `allocation_scalar` per rung that downstream sizing applies; persists rung state plus an append-only transition history.
- Notable:
  - Excellent docstring separating three orthogonal state machines: governance approval ("permitted to trade?"), health lifecycle ("performing?"), drawdown ladder ("how much capital risk?").
  - Real anti-flapping design implemented as **pure module-level functions** (`compute_target_ladder_state`, `apply_transition_rules`, `_recovery_threshold_for`) explicitly separated for testability: escalation always allowed (safety > anti-flap, except min_observation_cycles on first escalation from NORMAL); recovery requires cooldown expiry + hysteresis band undershoot + one-rung-at-a-time; BREACHED recovery can require explicit human ack (`acknowledge_breach`).
  - Observe/enforce dual mode: state is only persisted in `enforce` mode; OTel metrics emitted in both. Unconfigured limit (`max_drawdown_allowed <= 0`) treats utilization as fully breached in the pure helper (fail-safe) but the eval path skips strategies with missing data.
  - Exception swallowing (deliberate, bounded): `_load_config` has bare `except Exception: return DrawdownGovernanceLadderConfig()` (silent fallback to defaults — mildly risky, masks settings bugs); per-strategy eval wraps in `except Exception` returning a skipped evaluation (logged); audit-emit failures are caught and logged so audit failure can't break the run.
  - Same layering deviation: direct `session.scalars(select(StrategyGovernance))`, direct attribute mutation of ORM row in `_persist_state`/`acknowledge_breach`, direct `flush()/commit()`.

### src/autonomous_trading_platform/application/services/correlation_monitoring_service.py (965 lines)
- Purpose: Computes and persists rolling correlation and covariance snapshots at symbol/strategy/sector level over multiple windows (20/60/252), with cluster detection and numerical-stability diagnostics. Explicitly observability-only — never alters allocation or trading.
- Notable:
  - Solid numerical hygiene: log returns; excludes non-finite and zero-variance series with recorded warnings; covariance condition-number check (>1e12 → flagged unstable); positive-definite check via `eigvalsh`; SHA-256 hash (16 hex chars) of the sorted-JSON covariance matrix for lineage/dedup.
  - Hand-rolled union-find (path-halving) single-linkage clustering at |corr| ≥ 0.80 (`_detect_clusters`), high-correlation alert log at ≥ 0.90 constant (declared but `_HIGH_CORR_ALERT_THRESHOLD` is actually unused in code — smell: dead constant).
  - Pure math helpers at module level; per-record OTel metrics + structured log event names as constants.
  - Direct ORM: `session.scalars(select(MarketBar...))` and `StrategyLivePerformanceSnapshot` queries (persistence does go through `CorrelationSnapshotRepository`). Loads all bars ≤ as_of for symbols then trims tails in Python (no LIMIT per symbol) — memory-heavy for long histories.
  - No commit in service — persistence via repo insert; transaction ownership left to caller (inconsistent with governance services which self-commit).

### src/autonomous_trading_platform/application/services/factor_exposure_monitoring_service.py (849 lines)
- Purpose: Rolling factor-exposure snapshots (market beta via OLS vs SPY, momentum = trailing cumulative log return, annualized volatility, sector concentration, size = ln(market cap), quality/value metadata passthrough) aggregated symbol → strategy → portfolio, with concentration and drift alerts. Observability-only by design.
- Notable:
  - Methodology transparency is a first-class feature: `methodology()` dict of human-readable formulas persisted with every snapshot plus `factor_computation_version = "factor_exposure_v1"` and a `data_lineage` block — audit-friendly design.
  - Sane estimator guards: beta rejected if benchmark variance < 1e-12, |beta| > 10, or observations < minimum; weights normalized by gross (abs) exposure; NaN/Inf filtered everywhere.
  - Drift detection compares against last persisted portfolio exposure per window (threshold 0.25 abs change); concentration alerts attribute affected strategies (those with ≥ 50% of threshold).
  - Same direct-ORM read of MarketBar via `session.scalars`; loads full history ≤ as_of then trims in Python. Persistence via repository; no commit in service.
  - Minor smell: `_estimate_beta` truncates both series to last-n independently and aligns by tail length only — assumes symbol and benchmark return series are date-aligned, which holds only if both have bars for identical trading days (no timestamp join).

### src/autonomous_trading_platform/application/services/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/application/services/active_strategies_service.py (33 lines)
- Purpose: Thin read-model service that maps `ActiveStrategiesRepository` rows to plain dicts for the dashboard "active strategies" list.
- Notable: Textbook-thin orchestration — no logic beyond a dict comprehension; goes through the repository, no direct ORM/session use.

### src/autonomous_trading_platform/application/services/alpaca_portfolio_service.py (91 lines)
- Purpose: Builds portfolio summary/holdings/allocation views directly from live Alpaca broker data (account + positions), as an alternative to the DB-backed portfolio analytics path.
- Notable:
  - All money math done in `Decimal` via a `_d()` coercion helper that falls back to `Decimal("0")` on `InvalidOperation`/`TypeError` — defensive against malformed broker payloads, silently masks bad data rather than raising.
  - `strategy_id` is hardcoded to `"unknown"` per holding (Alpaca positions carry no strategy attribution) — allocation-by-strategy is therefore always a single "unknown" bucket; a functional gap rather than a bug.
  - No session/repository/ORM at all — this service's only I/O is through `AlpacaBrokerClient`, i.e., it bypasses the storage layer entirely (a different data path from the rest of the portfolio services, which read the SOR).

### src/autonomous_trading_platform/application/services/audit_log_service.py (77 lines)
- Purpose: Maps `AuditLogRepository` rows into `AuditLogEventResult`/`AuditLogListResult` dataclasses for the audit-log API/UI.
- Notable: Naive-datetime normalization (`replace(tzinfo=UTC)` if `tzinfo is None`) guards against SQLite's naive-datetime storage; actor/strategy_id/rationale are pulled out of a free-form `event_metadata` JSON blob rather than dedicated columns. No direct ORM use — repository only.

### src/autonomous_trading_platform/application/services/dataset_version_command_service.py (57 lines)
- Purpose: Creates `DatasetVersions` rows via `SorUnitOfWork`.
- Notable: Clean UoW usage (`with SorUnitOfWork(session) as uow: uow.dataset_versions.insert(...)`); session opened from an injected `session_factory` and always closed in `finally`. No direct ORM query/session mutation.

### src/autonomous_trading_platform/application/services/experiment_input_mapping.py (110 lines)
- Purpose: Pure function (`build_experiment_mapping`) translating a UI-facing (strategy_type, risk_level, time_horizon) triple into a full experiment configuration (parameter grid, filter thresholds, dataset/universe selection, stage configuration) via static lookup tables.
- Notable: No I/O, no session — a pure config-generation module, easy to unit test. Raises plain `ValueError` on unsupported enum values (no custom exception types, unlike the governance modules). `deepcopy` on returned config dicts prevents callers from mutating the shared module-level tables.

### src/autonomous_trading_platform/application/services/governance_exceptions.py (66 lines)
- Purpose: Three custom exceptions used by promotion/demotion flow: `MissingSourceRunError`, `PromotionRulesMissingError`, `PromotionCriteriaConfigurationError`.
- Notable: Each carries structured fields (strategy_id/target_state, from/to state, missing_fields/rule_id) plus a fully-formed human-readable message — built for the audit trail described in `auto_promotion_service.py`. All three encode a "fail closed" philosophy explicitly in their docstrings (missing evidence or null-required-criteria block the transition rather than defaulting to pass).

### src/autonomous_trading_platform/application/services/feature_dataset_command_service.py (82 lines)
- Purpose: Creates `FeatureDatasetVersion` contracts and registers them via `FeatureDatasetRegistrationService`; generates a version id (`feat_{name}_{uuid12}`) when one isn't supplied.
- Notable: Delegates persistence entirely to `FeatureDatasetRegistrationService(session=session)` rather than touching a UoW/repository itself — this command service is essentially an ID-generation + contract-building wrapper. Session lifecycle (`finally: session.close()`) same pattern as the other command services in this file group.

### src/autonomous_trading_platform/application/services/ingestion_run_command_service.py (79 lines)
- Purpose: Create / mark-completed / mark-failed lifecycle for `IngestionRuns` rows.
- Notable: Clean `SorUnitOfWork` usage throughout; `mark_ingestion_run_completed`/`_failed` raise a plain `ValueError` if the run isn't found (fail loud, not silent). No direct ORM outside the UoW-provided repository.

### src/autonomous_trading_platform/application/services/factor_neutralization_service.py (754 lines)
- Purpose: Optional, advisory-by-default portfolio factor-neutralization layer with three modes — `OBSERVE_ONLY` (record decomposition, no weight change), soft-penalty (`_soft_neutralize`, projected-gradient descent onto the simplex), and `HARD_CONSTRAINT` (soft-solve then verify feasibility, else fall back to original weights). Never mutates allocation state directly — returns a `FactorNeutralizationResult` for callers to apply.
- Notable:
  - Hand-rolled numerical optimizer: `_project_simplex` (sort-based simplex projection, O(n log n)), gradient step `w - w0 penalty + turnover limiting + sector-cap clipping`, iterated up to `max_iterations` with an L∞ convergence check — a real (if simple) constrained QP solved without scipy.
  - Fail-safe design: any `ValueError` during hard-constraint solving is caught and routed to `_fallback()`, which reverts to original (pre-neutralization) weights and records `infeasibility_reason` — the strategy portfolio is never left in an undefined state on solver failure.
  - `_build_result`/`_persist` write a fully audit-complete row (`FactorNeutralizationRunRow`) including pre/post exposures, exposure reduction, constraint utilization/violations/binding constraints, config snapshot, and warnings — consistent evidence-first pattern seen elsewhere in governance-adjacent services.
  - Directly instantiates `FactorNeutralizationRepository(session)` for persistence; no raw `session.scalars`/`commit` in this file (repository owns writes) — cleaner layering than the governance-ladder services audited above.
  - `get_latest_run` and repository-backed queries are the only reads; no ORM leakage.

### src/autonomous_trading_platform/application/services/governance_audit_service.py (775 lines)
- Purpose: Single canonical recorder for every governance decision (promotion, demotion, health-lifecycle transition, drawdown-ladder transition, supersession) producing a fully reconstructable `GovernanceAuditEventRow` — decision rationale text, criteria evaluated, metrics snapshot, source-run lineage, before/after state, and OTel counters — plus a query API (`get_decision`, `list_decisions`, `get_supersession_chain`).
- Notable:
  - **Verifies the "capital-bearing transitions require a source_run_id" invariant referenced in auto_promotion_service**: `_requires_source_run()` returns True for `AUTO_PROMOTION` trigger source or any paper/live target state; missing evidence triggers a `MISSING_GOVERNANCE_EVIDENCE` observability event even before recording the decision.
  - Every `record_*` method is a pure-evidence composer — builds a `GovernanceDecisionEvidence` object, converts to JSON via `evidence.to_jsonable()`, and calls `GovernanceAuditRepository._build_row(...)` (note: calls a "private" `_build_row` helper from outside the repository class — minor layering smell, an underscore-prefixed cross-module call) then `self._repo.record(row)`.
  - Human-readable rationale strings are built by pure, dependency-free module-level functions (`_build_promotion_rationale`, `_build_demotion_rationale`, `_build_health_rationale`, `_build_drawdown_rationale`) — fully unit-testable without DB access.
  - `_is_drawdown_escalation` uses an explicit severity ordinal map (`NORMAL=0 ... BREACHED=4`) to classify a ladder transition as `ESCALATED` vs `APPROVED` outcome — a small, clean piece of domain modeling.
  - No direct ORM session queries in this file — all persistence goes through `GovernanceAuditRepository`/`AuditLogRepository`; this is the cleanest-layered of the governance-adjacent files in this scope (contrast with auto_demotion/auto_promotion/drawdown_governance, which all bypass the UoW).

### src/autonomous_trading_platform/application/services/health/broker_health_service.py (254 lines)
- Purpose: One of the `DetailedSystemHealthService` sub-checks (TASK-613) — validates broker TCP connectivity, auth (via `get_account()`), order endpoint (`list_open_orders()`), and **broker reconciliation freshness** (staleness of the latest broker-cash snapshot, threshold 4h).
- Notable:
  - **Partial verification of "broker reconciliation" claim**: this file only checks reconciliation *freshness* (how long since the last snapshot) via `RuntimeSoakVerificationRepository.get_latest_broker_cash_snapshot()` — it does not perform reconciliation itself (matching broker state vs internal ledgers); that logic must live in `execution/` (out of this agent's a-l `application/services` scope) and was not verified here.
  - Uses a `Protocol` (`BrokerAccountClient`) rather than a concrete import of `AlpacaBrokerClient` for the client dependency — decouples the health check from a specific broker implementation.
  - `except Exception` around `get_account()`/`list_open_orders()` extracts `exc.response.status_code` via `hasattr` duck-typing (works for both requests-style and custom exception shapes) and classifies 401/403 as CRITICAL vs other errors as DEGRADED — a deliberately tiered severity, not a blanket swallow (error is always surfaced in the returned `HealthCheckResult`).
  - Broker client is optional (`None`-able) — connectivity/auth/order checks degrade gracefully to DEGRADED rather than crashing when no client is configured (useful for research/backtest environments with no live broker).

### src/autonomous_trading_platform/application/services/health/control_state_health_service.py (205 lines)
- Purpose: TASK-614 health sub-check reporting kill-switch, pause/trading-enabled, freeze, and risk-degradation state as `HealthCheckResult`s — explicitly documented as "informational," i.e., CRITICAL here means trading is intentionally blocked, not that something is broken.
- Notable: Composes `RuntimeSoakVerificationRepository` (control state + risk snapshot reads) with `TradingFreezeService.is_trading_frozen()` — cross-cutting read of the safety layer's live state. No direct ORM; no commit (read-only by design, matching its "health check" role).

### src/autonomous_trading_platform/application/services/health/data_pipeline_health_service.py (287 lines)
- Purpose: TASK-612 — checks freshness/lag of raw bars, feature datasets, trading manifests, ingestion lag, and feature lag against per-check configurable thresholds (raw bars stale >2h, feature lag critical >2h, etc.).
- Notable: All thresholds are constructor-injectable timedeltas with sane module-level defaults — good testability. `_check_feature_lag` computes two independent lag signals (features-behind-raw-bars vs features-behind-now) and reports both in metadata even though only "lag_from_now" drives the OK/DEGRADED/CRITICAL verdict — the "lag behind raw" number is informational only (a minor asymmetry, not a bug). Read-only, repository-only.

### src/autonomous_trading_platform/application/services/health/detailed_system_health_service.py (84 lines)
- Purpose: TASK-617 — aggregates six health sub-services (OTel, job, data-pipeline, market-session, broker, control-state) into one `DetailedSystemHealthReport` consumed by `GET /api/v1/system/health/detailed`.
- Notable: Pure composition root — imports `MarketSessionHealthService`/`OtelHealthService` from sibling files with basenames `m`/`o` (out of this agent's a-l scope; not audited here, covered by the m-z sibling). `_aggregate_status` uses the same CRITICAL > DEGRADED > OK precedence rule duplicated verbatim across every health file in this package (five near-identical copies of `_ok`/`_degraded`/`_critical`/`_derive_status` helpers across broker/control_state/data_pipeline/job — could be factored into a shared module; notable DRY smell across the whole `health/` package).

### src/autonomous_trading_platform/application/services/health/job_health_service.py (239 lines)
- Purpose: TASK-611 — detects stale (>15m RUNNING), hung (>30m), orphaned (>1h) job records, duplicate-concurrently-running jobs of the same name, and "dead" jobs (expected job name with no successful run in the last 2h, checked against a fixed `_EXPECTED_JOBS` tuple: market_ingestion_cycle, feature_pipeline_cycle, trading_cycle).
- Notable: `_check_stale`/`_check_hung`/`_check_orphaned` all call the same repository method (`list_stale_running_runtime_jobs`) with different cutoffs rather than three distinct queries — efficient reuse but means "stale" jobs are a superset of "hung" which are a superset of "orphaned" (each check re-lists jobs already reported by the previous, more lenient, check) — could double-report the same job across three checks by design (intentional escalating tiers, not a bug, but worth knowing when reading a health report). Read-only, repository-only, no ORM.

### src/autonomous_trading_platform/application/services/live_performance_metrics_service.py (474 lines)
- Purpose: Computes live (as opposed to backtested) per-strategy performance metrics — realized return, rolling Sharpe, drawdown, volatility, win rate, days-live, days-since-profitable — from real fills (`Fill` joined to `OrderIntents`) and `CashSnapshot` equity curves, then persists a `StrategyLivePerformanceSnapshot` and exposes `get_latest()`.
- Notable:
  - `compute_alpha(days_live, trade_count)` is a standalone module-level function computing a live-vs-backtest metrics blending weight, using the *minimum* of a days-based and trades-based ramp so a strategy can't be over-weighted toward live metrics on volume alone without also having survived enough calendar time (and vice versa) — a well-reasoned anti-gaming design, documented with an explicit schedule in the docstring.
  - FIFO lot matching (`_compute_round_trip_trades`) is long-only: SELL fills consume BUY lots in the order received; short-side (sell-first) activity is silently ignored — an explicit, documented modeling simplification, not a bug, but worth flagging as a coverage gap if the platform ever supports shorting.
  - Direct ORM reads via `self._session.scalars(select(...))` for `OrderIntents`/`CashSnapshot`/`Fill` (three separate direct-session queries) — same layering deviation pattern seen in the governance/monitoring services; persistence of the computed snapshot does go through `LivePerformanceSnapshotRepository`.
  - Sharpe/volatility guard against zero/negative variance (`variance <= 0: return None`) rather than dividing by zero or returning NaN — good numerical hygiene, consistent with `correlation_monitoring_service`/`factor_exposure_monitoring_service`.

### src/autonomous_trading_platform/application/services/platform_replay/__init__.py (12 lines)
- Purpose: Docstring-only module documenting the four hook naming conventions (`run_<domain>_at_timestamp`, `snapshot_<domain>_at_timestamp`, `apply_<domain>_event`, `inject_<failure>`) used by every file in this subpackage.

### src/autonomous_trading_platform/application/services/platform_replay/admin_hooks.py (110 lines)
- Purpose: Preflight validation hook for the platform backtest/replay runner — checks DB connectivity (`SELECT 1`), reads the alembic version, instantiates `Settings()` to confirm config loads, and warns if `NO_LIVE_TRADING` isn't set.
- Notable: `validate_admin_preflight` raises on config/DB errors are all caught and converted to warnings/errors in the result rather than propagating — appropriate for a "diagnose, don't crash" preflight tool. Direct raw SQL via `session.execute(text("SELECT 1"))` / `text("SELECT version_num FROM alembic_version LIMIT 1")` — deliberate low-level check (verifying migrations table exists), not a UoW/repository violation in the normal sense since this is infrastructure probing, not domain data access.

### src/autonomous_trading_platform/application/services/platform_replay/controls_hooks.py (212 lines)
- Purpose: Replay hooks for the "controls" domain — `snapshot_controls_at_timestamp` (read global control state), `apply_controls_event` (dispatch a timeline event like pause/resume/enable/disable/allocation-override to `RuntimeControlService`/`StrategyControlService`/`StrategyAllocationService`), `build_controls_summary` (artifact bundle read).
- Notable: `apply_controls_event` is a big if/elif dispatch over `event.event_type` strings with per-branch required-field validation (raises plain `ValueError` if a required field like `strategy_id` is missing for that event type) — correctly delegates to real services rather than mutating control-state rows directly, so it inherits those services' UoW/validation. Distinguishes `LookupError` (strategy not seeded → "skipped", not "failed") from generic `Exception` (→ "failed" with rollback) — a thoughtful two-tier error classification for a test/replay harness. Direct `session.scalars(select(StrategyControlState))` in `build_controls_summary` (read-only summary, bypasses repository).

### src/autonomous_trading_platform/application/services/platform_replay/diagnostics_hooks.py (114 lines)
- Purpose: Wraps `RuntimeSnapshotService.capture()` to produce a point-in-time diagnostics snapshot during replay, optionally writing it to a JSON file under the replay artifact directory.
- Notable: `except Exception: pass` in `build_diagnostics_summary` (silently returns defaults - portfolio_value=None, active_strategies=0 - on any capture failure) — a genuine silent-swallow, though scoped to a best-effort summary builder for artifact bundles rather than a decision path, so the blast radius is limited to reporting, not trading logic.

### src/autonomous_trading_platform/application/services/platform_replay/execution_hooks.py (108 lines)
- Purpose: Read-only execution-domain replay hook — snapshots open order count (via `SorUnitOfWork.tracked_orders`), fill-quality/adverse-fill counts (direct `FillQualityMetrics` query) for the platform artifact bundle.
- Notable: Both `snapshot_execution_at_timestamp` and `build_execution_summary` wrap their internal reads in bare `except Exception: pass`/`warnings.append(...)` — failures degrade to zero-counts rather than propagating, appropriate for an observability-only snapshot hook but a real exception-swallowing pattern (no logging on the `build_execution_summary` path — genuinely silent). `reconciliation_status` is always `None` in both functions — a declared-but-unimplemented field (broker reconciliation status is not actually wired into this hook, unlike the health-service reconciliation freshness check).

### src/autonomous_trading_platform/application/services/platform_replay/failure_injection.py (456 lines)
- Purpose: Test-only harness that writes synthetic failure evidence (missing/late bars, feature validation failures, risk-limit breaches, drawdown breaches, governance-demotion-triggering metrics, broker reconciliation mismatches, rejected orders, dead runtime jobs) directly into SOR tables so the platform replay runner can exercise error-handling paths without a real broker outage.
- Notable:
  - Explicitly documented as production-unsafe ("None of these should run in production (APP_ENV check is caller's responsibility)") — the safety check is delegated to the caller, not enforced here; a real gap if this module were ever invoked from a misconfigured environment, though the module docstring is upfront about it.
  - **Direct `session.add(...)` + `session.flush()` throughout — bypasses repositories/UoW entirely** (heaviest ORM-bypass file in the whole a-l scope), justified by its nature as a raw fixture-seeding tool rather than domain logic.
  - `inject_governance_demotion_trigger` returns `None` (typed `# type: ignore[return-value]`) when no `SimulationRuns` row exists for the strategy — caller-handled sentinel rather than an exception, a deliberate but type-unsafe convention.
  - `inject_broker_reconciliation_mismatch`/`inject_order_rejected` wrap their writes in bare `except Exception: return None` — silent swallow, but scoped to a test-injection utility.

### src/autonomous_trading_platform/application/services/platform_replay/features_hooks.py (167 lines)
- Purpose: Replay hook wrapping `run_feature_pipeline_cycle` — resolves the latest validated raw/adjusted-bars dataset version if not supplied, runs the pipeline for a single day, and classifies each computed feature as "computed" vs "reused" (dataset-versioning reuse) for the replay result.
- Notable: `lineage_ok` is set to `False` if any pipeline warning contains the substring `"lineage"` or `"mixed"` (case-insensitive) — a fragile string-matching heuristic for detecting mixed-price-basis lineage issues rather than a structured warning code/type. `build_feature_summary` iterates fixed feature names × both price bases to find "latest validated" versions — hardcoded feature name list (`returns, volatility, moving_average, liquidity, regime, regime_classification`) duplicated from the pipeline's own feature set.

### src/autonomous_trading_platform/application/services/platform_replay/governance_hooks.py (241 lines)
- Purpose: Replay hook that runs the full governance tick (auto-promotion → auto-demotion → health lifecycle) each timestamp, plus `apply_governance_event` for manual operator-driven transitions (`governance_manual_transition`, `health_review_acknowledged`) and `build_governance_summary` for the artifact bundle.
- Notable: Each of the three governance sub-steps (promotion/demotion/health) is wrapped in its own `try/except Exception` that appends a warning string and continues — one failing sub-step doesn't block the others, consistent with the per-candidate isolation pattern in `auto_demotion_service`/`auto_promotion_service`. Distinguishes `LookupError` (strategy not seeded → "skipped") from other exceptions (→ "failed"), same two-tier pattern as `controls_hooks.py`. `build_governance_summary` does a direct `session.scalars(select(StrategyGovernance))` (bypasses repository) purely for a count.

### src/autonomous_trading_platform/application/services/platform_replay/ingestion_hooks.py (310 lines)
- Purpose: Replay hooks wrapping `run_market_ingestion_cycle` and `run_corporate_action_ingestion_cycle`, plus `_compact_day_partitions` — a parquet-fragment compactor that merges per-cycle `part-*.parquet` files into one `data.parquet` per symbol/month partition (backtest mode only), deduplicating by `bar_id` and writing atomically (temp file + `.replace()`).
- Notable: `_compact_day_partitions` reads individual parquet files via `pq.ParquetFile(...).read()` rather than `pq.read_table()` specifically to avoid PyArrow's Hive dataset scanner walking the directory tree and raising `ArrowTypeError` on mixed `int32`/`dictionary<int32>` year-column types across partitions — a documented, non-obvious workaround for a real PyArrow footgun. Atomic write via temp-file-then-rename prevents partial-write corruption on crash mid-compaction. Both ingestion functions wrap `except Exception` narrowly around the underlying cycle call only (not broader), returning a clean `failed` result with the error message.

### src/autonomous_trading_platform/application/services/platform_replay/initial_state_hooks.py (345 lines)
- Purpose: Seeds a backtest fixture's `initial_state` YAML block into the DB before the first replay tick — operator settings patch, per-strategy governance state + strategy_configs rows, evaluation-checkpoint reset, allocation overrides, and a fallback base `CapitalAllocationPolicies` row per approval status (so `PortfolioEngine.get_allocation()` doesn't raise `NoPolicyFoundError` on a fresh DB).
- Notable:
  - `_STATE_MAP` explicitly documents a subtlety: fixture YAML uses short aliases (e.g. `approved_paper`) that must be canonicalized to the long-form DB strings (`approved_for_paper_trading`) used by the rest of the codebase, **not** the `GovernanceState` enum's short-form values — a real, documented naming inconsistency between fixtures and the DB schema.
  - Every per-entry seeding step (`settings`, per-strategy `governance`, `checkpoint_reset`, `allocations`, `capital_allocation_policies`) is individually wrapped in `try/except Exception` appending to an `errors` list rather than raising — partial-seeding-with-error-report design, appropriate for a fixture loader but means a broken fixture entry silently leaves that one strategy unseeded while others proceed.
  - `_upsert_strategy_governance`/`_upsert_allocation_override`/`_upsert_strategy_config` use **direct `session.query(...)`/`session.add(...)`/`session.get(...)`** — no repository/UoW at all (consistent with `failure_injection.py`, both being fixture/test-seeding utilities rather than production domain logic).
  - Final `session.flush()` is itself wrapped in `try/except` that calls `session.rollback()` and records the error — meaning a flush failure is swallowed into the returned summary rather than raised, so a caller checking only the return value (not `summary["errors"]`) could believe seeding succeeded when it didn't.

---

## Cross-cutting notes for this agent's range (a-l, application/services)

**lookahead_guard_service**: not present in `application/services/` at all (any casing/basename). The only match repo-wide is `src/autonomous_trading_platform/research/simulation/services/lookahead_guard_service.py`, which is outside this agent's assigned directory (`application/services/`) and was therefore not read or verified here — cannot confirm or refute the "rejects any bar at or after current simulation timestamp" claim from this scope.

**broker reconciliation**: Only `health/broker_health_service.py`'s `_check_reconciliation_freshness` was in scope, and it verifies *freshness* (time since last broker-cash snapshot) only — not the reconciliation logic itself (matching broker positions/cash against internal ledgers), which lives in `execution/` (out of scope for this agent).

**auto_demotion_service / auto_promotion_service / drawdown_governance_service**: confirmed as a real, coherent governance lifecycle state machine (see entries above) — ladder transitions delegate to `StrategyGovernanceService.transition()`, all decisions are recorded via `GovernanceAuditService` (verified in this batch), and capital-bearing promotions are fail-closed on missing `source_run_id` (enforced twice: once in `auto_promotion_service.py`'s transition gate, once independently in `governance_audit_service._requires_source_run()`).

**"Services never call the ORM directly" claim**: refuted for a majority of files in this scope. Clean (repository/UoW only): `dataset_version_command_service`, `ingestion_run_command_service`, `feature_dataset_command_service`, `factor_neutralization_service`, `governance_audit_service`, `active_strategies_service`, `audit_log_service`, all five `health/` files. Direct ORM/session use: `auto_demotion_service`, `auto_promotion_service`, `drawdown_governance_service`, `correlation_monitoring_service`, `factor_exposure_monitoring_service`, `live_performance_metrics_service` (all from earlier in this file), plus in this batch: `platform_replay/controls_hooks.py`, `governance_hooks.py` (summary builders only), and especially `platform_replay/failure_injection.py` and `initial_state_hooks.py` (heaviest — direct `session.add`/`session.query`/`session.get` throughout, but both are test/fixture-seeding utilities, not production trading-decision code).

**Exception-swallowing inventory (this batch)**: genuine silent swallows (no re-raise, no logging) found in `platform_replay/diagnostics_hooks.py` (`build_diagnostics_summary`), `platform_replay/execution_hooks.py` (both functions, no logging), `platform_replay/failure_injection.py` (`inject_broker_reconciliation_mismatch`, `inject_order_rejected`), and `platform_replay/initial_state_hooks.py` (final flush failure swallowed into `summary["errors"]` rather than raised). All are scoped to observability/test-harness code paths, not core trading/governance decisions — the governance-audit and factor-neutralization services in this batch have no silent swallows (all exceptions are caught into typed result/fallback objects with recorded reasons).

---

## (a) Standout candidates
- `src/autonomous_trading_platform/application/services/governance_audit_service.py` — the single canonical evidence-recorder for every governance decision in the platform; clean layering (repository-only), pure/testable rationale builders, and directly verifies the fail-closed `source_run_id` requirement.
- `src/autonomous_trading_platform/application/services/factor_neutralization_service.py` — hand-rolled constrained-optimization (simplex projection + projected gradient descent) with a genuine fail-safe fallback path; good numerical hygiene and full audit persistence.
- `src/autonomous_trading_platform/application/services/live_performance_metrics_service.py` — well-reasoned `compute_alpha` live/backtest blending schedule and FIFO lot-matching PnL computation; honest about its long-only modeling limitation.
- `src/autonomous_trading_platform/application/services/platform_replay/ingestion_hooks.py` — `_compact_day_partitions`'s documented PyArrow Hive-scanner workaround and atomic temp-file-rename write pattern is a nice piece of applied storage-engineering knowledge.

## (b) Gaps/smells
- Widespread duplication of `_ok`/`_degraded`/`_critical`/`_derive_status` helper functions across all five `health/` files (near-identical ~15-line blocks each) — should be a shared module.
- `alpaca_portfolio_service.py` hardcodes `strategy_id="unknown"` for every holding — allocation-by-strategy is non-functional on this code path.
- `platform_replay/failure_injection.py` and `initial_state_hooks.py` bypass the UoW/repository layer entirely (acceptable for test-seeding utilities, but worth flagging since the project's stated invariant is "repositories only").
- `platform_replay/execution_hooks.py` declares `reconciliation_status` in its result contracts but never populates it (always `None`) in either function in this file.
- `lookahead_guard_service.py` is not in `application/services/` — lives in `research/simulation/services/` — could not be verified from this agent's assigned scope.

## (c) Coverage
Read 33 of 33 files in scope (5 covered by the prior partial run: `auto_demotion_service.py`, `auto_promotion_service.py`, `drawdown_governance_service.py`, `correlation_monitoring_service.py`, `factor_exposure_monitoring_service.py`; 28 covered in this session, listed above). No files skipped.
