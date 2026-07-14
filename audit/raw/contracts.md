# Audit: src/autonomous_trading_platform/contracts/

## Verified counts

```
$ find src/autonomous_trading_platform/contracts -name '*.py' | wc -l
92

$ find src/autonomous_trading_platform/contracts -name '*.py' -exec wc -l {} + | tail -1
5673 total

$ grep -rc "^class " src/autonomous_trading_platform/contracts --include='*.py' | awk -F: '{s+=$NF} END {print s}'
252

$ grep -rhoE "^class \w+\(([^)]*)\)" ... | sed ... | sort | uniq -c | sort -rn
    109 BaseModel
     34 enum.StrEnum
     30 StrEnum
     16 DomainReplayResult      # itself a BaseModel (platform_replay.py:71)
      1 Generic[T]              # Rule[T] in validators/core.py (NOT a BaseModel)

$ grep -rc "@dataclass" ... | sum
63 dataclass decorators (36 with frozen=True, 27 without)

$ grep -rnE "TODO|FIXME|XXX" src/autonomous_trading_platform/contracts --include='*.py' | wc -l
0
```

**Class taxonomy (sums exactly to 252):** 125 Pydantic models (109 direct `BaseModel` subclasses + 16 `DomainReplayResult` subclasses) + 64 StrEnum enums + 63 dataclasses.

**Immutability:** NOT uniform. No Pydantic model in contracts/ uses `frozen=True` — the only `model_config`/`ConfigDict` usage in the whole package is `simulation/dividend_event.py`. Immutability exists only in the 36 `@dataclass(frozen=True)` classes; 27 dataclasses are mutable, and all 125 Pydantic models are mutable by default.

**Domains (10 subpackages):** accounting, common, execution, governance, market, runtime, shadow, simulation, trading, validators. Runtime is by far the largest (34 files).

---

## Per-file entries

### contracts/__init__.py, accounting/__init__.py, common/__init__.py, execution/__init__.py, governance/__init__.py, market/__init__.py, runtime/__init__.py, simulation/__init__.py, trading/__init__.py, validators/__init__.py (0 lines each)
- Purpose: Empty package markers (10 of the 11 __init__.py files are 0 lines; shadow/__init__.py is the exception, see below).

### contracts/accounting/cash_snapshot.py (26 lines)
- Purpose: CashSnapshot Pydantic model — point-in-time cash/buying-power/equity state tied to a run_id.
- Notable: Settlement-aware fields (settled_cash/unsettled_cash) added under feature tag "F-06" with explicit legacy semantics ("None → treat all cash as settled") — real T+1 settlement domain awareness. Uses shared Money (Decimal) and UTCDateTime types.

### contracts/accounting/position_snapshot.py (27 lines)
- Purpose: Position + PositionSnapshot models — holdings with avg cost, market value, unrealized PnL, tagged with OrderSource provenance.

### contracts/accounting/risk_snapshot.py (28 lines)
- Purpose: RiskSnapshot — gross/net exposure, leverage, drawdown, plus is_blocked/block_reasons for gate decisions.
- Notable: limits/utilization are loosely typed dict[str, Any] — pragmatic but weakens the contract.

### contracts/common/enums.py (180 lines)
- Purpose: 24 StrEnum vocabularies shared across all domains: Side, OrderType, TimeInForce, OrderSource, OrderStatus, OrderEvent, CorporateActionType (8 kinds incl. spinoff/merger variants), LiquiditySide, BarInterval, PriceBasis (raw/adjusted), RunType (9 kinds), SignalDirection, MarketSession, BarQualityFlag, IntentExecutionStatus, StrategyEvent, StrategyState (6-state strategy FSM), CheckpointScope/Status, UniverseStatus (candidate→proposed→active→retired lifecycle), UniverseSource, RawSymbolStatus, AssetType, RawPoolRefreshCadence.
- Notable: Domain depth visible in small details: BarQualityFlag (late/suspected_outlier/missing_cycle_peer), OrderStatus includes pending_new/pending_cancel transitional states, IntentExecutionStatus.SHADOW_SUPPRESSED for shadow-mode runs.

### contracts/common/types.py (26 lines)
- Purpose: Shared primitives: UTCDateTime = Annotated[datetime, AfterValidator(enforce_utc)] rejects naive datetimes and normalizes to UTC; Money and Quantity are aliases of Decimal.
- Notable: The single most-leveraged file — every timestamp in the system is UTC-enforced at parse time via Pydantic Annotated types. Money=Decimal (no float money anywhere in contracts).

### contracts/market/corporate_action.py (32 lines)
- Purpose: CorporateAction model — action_id, type, effective/announced/record/payable dates, split_ratio, cash_amount, new_symbol, provenance (source, ingested_at).

### contracts/market/market_bar.py (36 lines)
- Purpose: MarketBar OHLCV model with interval, vwap, trade_count, price_basis (raw vs adjusted), adjustment_factor, session, quality_flags, ingestion provenance.
- Notable: price_basis + adjustment_factor pairing supports both raw and split/dividend-adjusted series — key for survivorship/adjustment correctness. quality_flags default `= []` (safe in Pydantic v2, which deep-copies defaults).

### contracts/simulation/dividend_event.py (41 lines)
- Purpose: DividendEvent for total-return simulation accounting; docstring specifies exact semantics (cash credited at open of ex_date, first-bar-only firing, no settlement delay).
- Notable: The ONLY frozen Pydantic model in the entire package (model_config = ConfigDict(frozen=True)) and one of the few with field_validators (symbol normalized to upper, amount > 0).

### contracts/trading/broker_order.py (47 lines)
- Purpose: BrokerOrder — broker-side order state mirroring Alpaca's order object, keyed to intent_id/run_id, with lifecycle timestamps.
- Notable: `broker: Literal["alpaca"]` hard-pins the broker at the type level. Four latency-chain timestamps (signal_generated_at → submitted_to_broker_at → broker_acknowledged_at → first_fill_at) enable end-to-end latency attribution. raw_broker_payload preserved for forensics.

### contracts/trading/cost_breakdown.py (12 lines)
- Purpose: CostBreakdown — commission/spread/slippage/total transaction cost decomposition, all Decimal Money.

### contracts/trading/fill.py (29 lines)
- Purpose: Fill — execution record linking broker_order_id + intent_id + run_id, with fees, maker/taker liquidity side, venue.

### contracts/trading/order_intent.py (34 lines)
- Purpose: OrderIntent — the pre-broker declared order, with idempotency_key and client_order_id for exactly-once submission, bar_timestamp for point-in-time provenance.
- Notable: qty XOR notional both optional here — mutual-exclusion is enforced in validators/order_intent.py, not in the model.

### contracts/trading/portfolio_signal.py (143 lines)
- Purpose: Contracts for the two-phase portfolio construction pipeline ("Recommendation 6.5"): SignalBatch (raw) → PortfolioSignal (netted per symbol) → SignalIntent (constraint-applied) → OrderIntent, plus PortfolioConstructionDiagnostics and PortfolioConstructionResult.
- Notable: Strong docstrings documenting each pipeline phase. Full attribution chain preserved (contributing strategies with weights survive netting/suppression) for governance and PnL attribution. Diagnostics contract exposes gross→net exposure transformation at every phase (raw_gross_signal_exposure, exposure_before/after_constraints, suppressed_notional_usd) — genuine portfolio-construction domain depth. Smell: notional/exposure fields are float here while trading fills use Decimal Money.

### contracts/trading/signal.py (27 lines)
- Purpose: Signal — per-strategy per-symbol signal with direction, confidence, target_position; enriched later with strategy_version, feature_snapshot_ref, target_exposure (all backward-compatible optionals).

### contracts/trading/signal_aggregate.py (60 lines)
- Purpose: Cross-strategy signal aggregation contracts: SignalNettingPolicy (9 values), StrategySignalContribution, SignalAggregationConflict, AggregatedSignalBundle.
- Notable: SignalNettingPolicy carries duplicate alias values (SUPPRESS_CONFLICTS aliases CONSERVATIVE etc.) — commented but the aliases are distinct enum members with different string values, not true aliases; both spellings can round-trip which is a mild smell.

### contracts/trading/slippage_measurement.py (21 lines)
- Purpose: SlippageMeasurement — reference (mid at submission) vs fill price, per-share/notional/bps slippage, all Decimal.

### contracts/execution/execution_plan.py (30 lines)
- Purpose: `ChildOrderIntent` frozen dataclass — one sliced child order produced by an execution policy (TWAP/VWAP), carrying parent/child intent IDs and bar_offset for scheduling.
- Notable: Deterministic child_intent_id documented as UUID5(parent.intent_id + slice_index + policy_mode) — reproducible IDs for replay/testing.

### contracts/execution/execution_policy_config.py (151 lines)
- Purpose: `ExecutionPolicyConfig` and mode sub-configs (LimitOrderPolicy, TWAPConfig, VWAPLiteConfig, SlippageConfig) governing how an OrderIntent is sliced/priced at execution time; `PolicyMode` enum (passthrough/market/limit/twap/vwap_lite).
- Notable: Has real business-logic-adjacent behavior embedded in the contract: a `@model_validator(mode="after")` enforcing mode-specific sub-config presence, plus `to_dict`/`from_dict` and factory classmethods (`passthrough()`, `market_only()`) — more than a "pure data shape," blurring the contracts/application boundary. Docstring embeds calibration guidance (adverse-slippage bps by policy type) as executable documentation.

### contracts/execution/execution_policy_result.py (33 lines)
- Purpose: `ExecutionPolicyResult` + `OrderSlice` — output of applying an ExecutionPolicyConfig to an OrderIntent (transformed intent, slices, expected slippage/cost).
- Notable: Mutable default args `slices: list[...] = []` and `policy_metadata: dict[...] = {}` at class body level — works under Pydantic v2 (deep-copied per instance) but would be a classic bug in a plain dataclass/function; relies on Pydantic-specific semantics.

### contracts/execution/fill_quality_record.py (53 lines)
- Purpose: `FillQualityRecord` — per-fill latency + slippage + cost quality metrics (signal→submission→fill latency chain, slippage bps, adverse-fill flag).
- Notable: Good domain depth: separates "expected_fill_price" (model estimate) from "fill_price" (actual) to measure model accuracy (`fill_vs_expected_bps`), distinct from slippage vs reference price.

### contracts/execution/simulation_vs_paper_comparison.py (214 lines)
- Purpose: Contracts for comparing simulated fills vs real paper/live fills: `PaperFillRecord`, `SimulatedFillRecord` (frozen dataclasses), `DivergenceFlag` enum (9 flags), `SimulationVsPaperComparisonRow/Summary/Comparison` (mutable dataclasses).
- Notable: Extensive module docstring specifying exact construction recipes and 3-tier matching key priority (intent_id → parent_intent_id+slice aggregation → client_order_id) — real sim/live parity-testing domain logic captured as data contracts. Largest execution/ file.

### contracts/governance/drawdown_governance.py (258 lines)
- Purpose: Per-strategy drawdown governance ladder (NORMAL→WARNING→PROBATION→SUSPENDED→BREACHED): `DrawdownGovernanceLadderConfig` (frozen dataclass with ~25 tunable thresholds/cooldowns), `DrawdownGovernanceLadderEvaluation`/`RunResult` (frozen dataclasses), plus Pydantic API-response models.
- Notable: Contains actual behavior functions (`ladder_severity`, `ladder_state_one_step_better`) and a `match`-based method (`allocation_scalar_for`, `cooldown_hours_for`) on the frozen config dataclass — again blurs "no business logic" boundary, though arguably just config-lookup logic. Hysteresis band + per-rung cooldown + `min_observation_cycles` show real anti-flapping design for a live capital-allocation governor.

### contracts/governance/governance_audit.py (159 lines)
- Purpose: Immutable governance audit trail: `TriggerSource`/`DecisionOutcome` enums, `GovernanceCriteriaEvaluation`, `GovernanceDecisionEvidence` (full decision snapshot incl. lineage IDs), `GovernanceAuditRecord`, `GovernanceAuditListResult`.
- Notable: `GovernanceDecisionEvidence` docstring states evidence "must fully explain the decision without any external lookups" — an explicit reproducibility/auditability invariant; carries `to_jsonable()` serialization methods on frozen dataclasses (duplicating what Pydantic would give for free — mild inconsistency in contract style across the package, dataclass-with-manual-serialization vs BaseModel).

### contracts/governance/strategy_governance.py (18 lines)
- Purpose: `StrategyGovernanceRecord` — links a strategy config_hash + experiment_id + source simulation run to its current governance state.
- Notable: **Architecture smell**: imports `GovernanceState` from `autonomous_trading_platform.governance.models.governance_state` — i.e. a contracts/ module reaching outward into a non-contracts business-logic package (`governance/`), which per CLAUDE.md's layering ("interfaces → application → domain → storage → contracts", contracts should not depend on outer layers) is a boundary violation, or at minimum makes this contract depend on a mutable domain enum rather than defining its own.

### contracts/governance/strategy_health.py (66 lines)
- Purpose: `StrategyHealthStatus` enum (healthy/watch/degrading/critical/suspended) plus `StrategyQualityScoreRecord`, `StrategyHealthStateRecord`, `StrategyHealthSummary` Pydantic models for the strategy-quality/health-monitoring subsystem.
- Notable: `StrategyHealthStatus.SUSPENDED` docstring explicitly distinguishes operational suspension from governance revocation — a real domain nuance (health monitor can halt allocation without touching the separate governance approval state machine).

### contracts/governance/strategy_health_lifecycle.py (197 lines)
- Purpose: Health-lifecycle threshold configuration and evaluation results: `HealthLifecycleConfig` (frozen dataclass, ~30 fields — watch/degrading/critical thresholds × 6 metrics, cooldowns, allocation penalties), `LifecycleEvalMetrics`, `LifecycleTransitionEvent`, `LifecycleStrategyResult`, `LifecycleRunResult`, plus 2 Pydantic API summary models.
- Notable: Very deep tunable-threshold surface (3 severity tiers × drawdown/quality-decline/Sharpe/quality-score/win-rate/consecutive-negative-periods = 18 threshold constants) mirroring drawdown_governance.py's ladder pattern — same anti-flapping/cooldown/hysteresis design repeated for a second, operationally-distinct governor (health vs drawdown).

### contracts/runtime/audit_log.py (17 lines)
- Purpose: `AuditLogEvent` — generic system audit event (event_type, component, message, metadata) tied to an optional run_id.

### contracts/runtime/blended_metrics_summary.py (48 lines)
- Purpose: `BlendedMetricsSummary` — confidence-weighted blend of research vs live metrics (`blended_score = alpha*live + (1-alpha)*research`) for adaptive allocation/quality scoring.
- Notable: Explicit docstring invariant: "Never used as governance evidence — promotion/demotion workflows must reference ResearchMetricsSummary or LiveMetricsSummary directly" — a real anti-gaming design decision preventing blended/smoothed numbers from driving capital decisions.

### contracts/runtime/broker_account_snapshot.py (23 lines)
- Purpose: `BrokerAccountSnapshot` frozen dataclass — point-in-time broker account state (cash/buying_power/equity/portfolio_value) tagged by broker + trading_environment.

### contracts/runtime/correlation_snapshot.py (89 lines)
- Purpose: Correlation/covariance monitoring contracts: `CorrelationSnapshotType` enum, `CorrelatedPair`, `ClusterInfo`, `CorrelationSnapshotRecord`, `CovarianceSnapshotRecord`, `CorrelationMonitoringRunResult`.
- Notable: Numerical-stability fields baked into the contract itself (`is_numerically_stable`, `condition_number`, `is_positive_definite`) — the contract records not just the result but whether the underlying linear algebra was well-conditioned, useful for catching silent covariance blow-ups.

### contracts/runtime/dataset_version.py (28 lines)
- Purpose: `DatasetVersion` — versioned raw market dataset metadata (source, price_basis, interval, schema_version, symbol/date coverage, checksum) for the Parquet dataset-versioning system.

### contracts/runtime/detailed_health.py (75 lines)
- Purpose: System health-check taxonomy: `HealthStatus`/`HealthSeverity` enums, a 20-value `HealthCheckName` enum (OTel exporters, job staleness, data freshness, broker connectivity, kill-switch/pause/freeze/degradation control state), `HealthCheckResult`, `ServiceHealthReport`, `DetailedSystemHealthReport`.
- Notable: `HealthCheckName` enumerates concrete operational failure modes (job_hung, job_duplicate_running, job_orphaned, broker_reconciliation_freshness) showing real production-incident taxonomy rather than generic "ok/error" health.

### contracts/runtime/experiment.py (25 lines)
- Purpose: `Experiment` — research experiment metadata (strategy_set, parameter_grid, dataset/universe version pins, time window) for the experiment pipeline.

### contracts/runtime/factor_exposure.py (103 lines)
- Purpose: Factor-exposure computation contracts across 3 levels (symbol/strategy/portfolio): `FactorName` (7 factors incl. market_beta/momentum/volatility/sector/size/quality/value), `FactorExposureInputPosition`, `FactorExposureValue`, `SymbolFactorExposure`, `StrategyFactorExposure`, `PortfolioFactorExposure`, `FactorExposureSnapshotRecord`, `FactorExposureRunResult`.
- Notable: Each `FactorExposureValue` carries its own `methodology`, `observations_used`, `is_valid`, `warnings` — per-factor-per-asset provenance rather than a single aggregate confidence flag.

### contracts/runtime/factor_neutralization.py (88 lines)
- Purpose: Contracts for constraining/neutralizing portfolio factor exposure during optimization: `FactorNeutralizationMode` (observe_only/soft_penalty/hard_constraint), `FactorConstraintType`, `FactorNeutralizationStatus`, `FactorNeutralizationConstraint/ConfigPayload/Request/Result`, `FactorExposureDecomposition`.
- Notable: `FactorNeutralizationMode.OBSERVE_ONLY` mirrors the observe/enforce staged-rollout pattern seen in drawdown/health governance — a consistent platform-wide convention for introducing risk controls in shadow mode before enforcing them.

### contracts/runtime/feature_dataset_audit_record.py (37 lines)
- Purpose: `FeatureDatasetAuditRecord` frozen dataclass — audit-trail view of a feature dataset version, embedding the full upstream `DatasetVersion` for lineage traceability plus computation_code_version.

### contracts/runtime/feature_dataset_version.py (30 lines)
- Purpose: `FeatureDatasetVersion` frozen dataclass — versioned feature (derived) dataset metadata, parallel to `DatasetVersion` but for computed features with `source_dataset_version` back-reference and `computation_code_version`.

### contracts/runtime/ingestion_run.py (21 lines)
- Purpose: `IngestionRun` — record of one data-ingestion execution (RunType, source, dataset_version, status, row/file counts, error_message).

### contracts/runtime/live_metrics_summary.py (43 lines)
- Purpose: `LiveMetricsSummary` — metrics computed exclusively from realized live/paper fills (rolling Sharpe, realized drawdown, win rate) with `MetricLineageMetadata` attached.
- Notable: Docstring invariant: "Never sourced from backtests or simulations" — same lineage-purity discipline as BlendedMetricsSummary, preventing simulated numbers from contaminating live performance claims.

### contracts/runtime/live_performance_metrics.py (36 lines)
- Purpose: `LivePerformanceMetrics` — near-duplicate of LiveMetricsSummary's field set (same realized_return/rolling_sharpe/drawdown/win_rate fields) but with lineage fields declared optional "for backward compatibility."
- Notable: Code smell — two contracts (this and `live_metrics_summary.py`) carry almost identical field sets with no inheritance/composition relationship; looks like an in-place migration to the lineage-typed model was done by adding a parallel contract rather than evolving the original, risking drift between the two.

### contracts/runtime/metric_lineage.py (34 lines)
- Purpose: `MetricLineageType` enum (research/live/blended) + `MetricLineageMetadata` — shared provenance envelope (environment, calculation_version, generated_at, lookback window, source run/strategy IDs) attached to all metrics-summary contracts.
- Notable: Explicitly designed so "dashboards and governance workflows can display or filter by lineage without joining auxiliary tables" — denormalization-for-auditability as a deliberate contract design choice.

### contracts/runtime/metrics_summary.py (31 lines)
- Purpose: `MetricsSummary` — backtest/research-run metrics (total_return, sharpe_ratio, max_drawdown, trade counts) with the same bolted-on optional lineage fields pattern as `live_performance_metrics.py`.

### contracts/runtime/optimization_backend.py (121 lines)
- Purpose: Solver-agnostic portfolio optimization contracts: `OptimizationBackendConfig` (solver name/timeout/tolerance, fallback_solver_order, PSD repair), objective/constraint term models (6 objective types incl. minimize_factor_exposure, 9 constraint types), `OptimizationProblem`, `OptimizationBackendResult`.
- Notable: `OptimizationBackendConfig` defaults name real solvers (CLARABEL, OSQP, SCS) with `covariance_regularization`/`diagonal_jitter`/`repair_psd` — genuine convex-optimization engineering knowledge (numerical conditioning of covariance matrices) captured directly in the contract defaults. `input_hash` on the result enables reproducibility/caching.

### contracts/runtime/optimizer.py (73 lines)
- Purpose: Higher-level/older MVO optimizer contracts: `OptimizationObjective` (4 values), `SolverStatus`, `FallbackMode`, `OptimizerConstraints`, `OptimizationResult`.
- Notable: Overlaps heavily with `optimization_backend.py` (near-duplicate SolverStatus/FallbackMode/ObjectiveType enums with different member sets and a differently-shaped result contract) — looks like two generations of the optimizer contract layer coexisting rather than one being retired, a maintenance/consistency smell worth flagging alongside live_performance_metrics.py/live_metrics_summary.py.

### contracts/runtime/platform_replay.py (490 lines)
- Purpose: The full contract surface for the "platform replay"/backtest-runner harness: `PlatformReplayContext` (mutable dataclass threading run_id/actor/dry_run through every domain hook), `DomainReplayResult` base (Pydantic, one `Literal["ok","skipped","failed","dry_run"]` status) with 16 domain-specific subclasses (Admin/Ingestion/Feature/Universe/TradingCycle/Risk/Governance/Portfolio/Controls/Settings/Safety/Operations/Execution/Research/Diagnostics/StrategyCatalog), 16 parallel plain-dataclass "Summary" types for the artifact bundle, 4 Timeline event dataclasses (Safety/Controls/Settings/Governance, each with a `Literal[...]` closed event_type vocabulary), and `PlatformBacktestArtifact` (the top-level output bundle with a custom recursive `to_dict()` that walks dataclasses/BaseModels/Decimals/UUIDs into JSON-safe primitives).
- Notable: By far the largest file in contracts/ (490 lines, ~35 classes) — it is effectively a full-platform "one hook result type per subsystem" registry, showing every domain that the replay/backtest harness touches. `DomainReplayResult` is deliberately Pydantic ("so **base unpacking is mypy-clean") while its result subclasses use it, but the artifact bundle dataclasses avoid Pydantic — evidence of a considered but non-uniform choice per subclass. This is also the source of the earlier count anomaly: `DomainReplayResult` subclasses (16 of them) are Pydantic models, not a distinct taxonomy bucket.

### contracts/runtime/raw_market_symbol.py (50 lines)
- Purpose: `RawMarketSymbol` (broker/provider raw symbol universe entry with tradability/marginable/shortable/fractionable flags), `RawMarketPoolSnapshot`, `RawMarketPoolMembership` — pre-universe-filtering raw asset pool tracking.

### contracts/runtime/reconciliation_report.py (52 lines)
- Purpose: Broker reconciliation contracts: `DriftSeverity`, `ReconciliationCheckType` (orders/fills/positions/cash/equity), `ReconciliationStatus`, `ReconciliationCheckResult` and `ReconciliationReport` (both frozen dataclasses, report using an immutable `tuple[...]` for checks rather than a list).
- Notable: Use of `tuple[ReconciliationCheckResult, ...]` instead of `list[...]` for true immutability inside a frozen dataclass — one of the few places in the package where immutability is enforced all the way down rather than just at the top level (a frozen dataclass with a mutable list field is only shallow-frozen).

### contracts/runtime/research_metrics_summary.py (45 lines)
- Purpose: `ResearchMetricsSummary` — metrics derived exclusively from backtests/simulations/experiments, with a `win_rate` computed `@property`.
- Notable: Docstring: "Immutable after creation... Governance promotion workflows use this type to enforce that promotion evidence is always traceable to a specific simulation run" — yet the model is NOT actually `frozen=True` (no ConfigDict), so the immutability claim is asserted in prose only, not enforced by Pydantic. This directly contradicts the earlier finding that dividend_event.py is the only frozen Pydantic model — worth flagging as a doc/implementation gap.

### contracts/runtime/risk_budget.py (94 lines)
- Purpose: Risk-parity/risk-budgeting allocation contracts: `AllocationMode` (equal_capital/equal_risk_contribution/fixed_risk_budgets/inverse_volatility), `FallbackReason` (6 covariance-failure modes), `StrategyRiskContribution`, `RiskBudgetingRunResult` (with computed `max_risk_concentration`/`hidden_concentration_detected` properties), `RiskBudgetSnapshotRecord`.
- Notable: `hidden_concentration_detected` property encodes real risk-management logic directly on the contract (flags when any strategy's variance contribution exceeds 2x its equal-weight share) — another instance of behavior living in what's nominally a pure data contract.

### contracts/runtime/run_manifest.py (71 lines)
- Purpose: `RunManifest` — the master reproducibility record for any run (backtest/paper/live): broker, strategy version/config, capital bucket, dataset/universe version pins, git_commit, docker_image, dependency_lock_hash, random_seed, plus corporate-action/dividend lineage fields (dividend_events_hash, price_adjustment_basis) and settlement_days.
- Notable: Same architecture smell as `governance/strategy_governance.py`: imports `GovernanceState` from `autonomous_trading_platform.governance.models.governance_state` — a contracts/ module depending on the governance/ business-logic package. `git_commit`/`docker_image`/`dependency_lock_hash` fields show serious reproducibility engineering (full environment pinning per run, not just data versions).

### contracts/runtime/runtime_job_run.py (22 lines) / runtime_job_run_step.py (20 lines)
- Purpose: `RuntimeJobRun` and `RuntimeJobRunStep` — frozen dataclasses tracking Airflow/scheduler job executions and their sub-steps (status, timing, correlation_id, error info) for observability.

### contracts/runtime/runtime_snapshot.py (145 lines)
- Purpose: The API/UI-facing "give me everything on one screen" aggregate: `RuntimeSnapshot` composes ~15 sub-models (PortfolioSnapshot, OperatorControlsSnapshot, OperatorSettingsSnapshot, StrategyControlEntry/AllocationEntry, AggregateAllocationSnapshot, DatasetVersionEntry, RecentActivityEntry, ExperimentEntry) into one dashboard payload.
- Notable: This is the contract most directly serving the frontend Dashboard/Portfolio pages described in CLAUDE.md — field names (`todays_pnl_percent`, `current_drawdown`, `average_pairwise_correlation`) map closely to UI copy, suggesting the contract was designed API-first for the frontend mockup's eventual real data source.

### contracts/runtime/runtime_soak_verification.py (65 lines)
- Purpose: `RuntimeSoakStatus`/`Severity`/`CheckName` (15 named soak-test checks incl. duplicate-fill-protection, cash/position/equity consistency, Loki ingestion) + `RuntimeSoakCheckResult`/`RuntimeSoakVerificationReport` (with `failed_checks`/`warning_checks` filtering properties) — a post-deployment/long-running-soak health verification report, distinct from `detailed_health.py`'s live health checks.
- Notable: `DUPLICATE_FILL_PROTECTION` and `CASH_POSITION_EQUITY_CONSISTENCY` checks show awareness of classic trading-system correctness bugs (double-filling, ledger drift) being explicitly soak-tested for.

### contracts/runtime/simulation_run.py (28 lines)
- Purpose: `SimulationRun` — one backtest/simulation execution record (strategy_id, dataset/universe version, symbols, date range, `window_role` for train/test/fold_N cross-validation splits).

### contracts/runtime/strategy_config.py (17 lines)
- Purpose: `StrategyConfig` — versioned strategy parameter set keyed by config_hash for reproducibility/governance linkage.

### contracts/runtime/ticker_lifecycle_event.py (24 lines)
- Purpose: `TickerLifecycleEventType` (rename/delisting/merger/successor) + `TickerLifecycleEvent` — symbol lifecycle tracking for survivorship-bias elimination in the universe subsystem.

### contracts/runtime/universe_snapshot.py (26 lines)
- Purpose: `UniverseSnapshot` — an older universe-definition contract.
- Notable: File header explicitly states `# DEPRECATED: UniverseSnapshot has been superseded by UniverseVersion + UniverseMember... Retained only for the schema drift test backward-compatibility pair` — a rare, honestly-labeled piece of deliberate legacy debt (contrasts with the unlabeled overlaps in optimizer.py/live_performance_metrics.py).

### contracts/runtime/universe_version.py (33 lines)
- Purpose: `UniverseVersion` (status, effective_from/to, rebalance_reason, config_hash) + `UniverseMember` (rank/score/included_reason/excluded_reason/liquidity+quality metrics JSON) — the current universe versioning model that replaced UniverseSnapshot.

### contracts/shadow/__init__.py (45 lines)
- Purpose: Package `__init__` re-exporting all shadow/ public names via explicit `__all__` — the one non-empty `__init__.py` in the whole contracts/ package.
- Notable: Unlike every other subpackage (which use empty `__init__.py`), shadow/ curates a public API surface — inconsistent init-file convention across the package, though harmless.

### contracts/shadow/comparison_results.py (127 lines)
- Purpose: 8 frozen dataclasses (`SignalComparisonResult`, `AllocationComparisonResult`, `RiskComparisonResult`, `ExecutionComparisonResult`, `FeatureComparisonResult`, `OptimizerComparisonResult`, `RuntimeComparisonResult`, `OutcomeComparisonResult`) — per-domain sim-vs-live comparison rows for the shadow-mode validation subsystem, each carrying its own `divergences: list[DivergenceRecord]`.
- Notable: Mirrors the domain taxonomy seen in `platform_replay.py`'s `DomainReplayResult` subclasses (signals/allocation/risk/execution/features/optimizer/runtime/outcome) — the codebase consistently decomposes the trading pipeline into the same ~8 domains across multiple unrelated subsystems (replay, shadow validation), suggesting a stable mental model of the system's phases.

### contracts/shadow/divergence.py (67 lines)
- Purpose: `DivergenceType` (11 kinds: data/feature/allocation/optimizer/covariance-instability/orchestration-timing/execution-degradation/risk/signal/outcome drift), `DivergenceCategory` (8 groupings), `DivergenceThresholds` (frozen dataclass, 11 tunable drift limits e.g. max_weight_drift_pct=0.02, max_execution_slippage_bps=15.0), `DivergenceRecord`.
- Notable: `DivergenceThresholds` defaults encode calibrated real-world tolerances (2% weight drift, 15bps slippage, 30s orchestration timing) — concrete evidence of having reasoned about acceptable sim/live parity bounds rather than picking arbitrary numbers.

### contracts/shadow/shadow_run.py (71 lines)
- Purpose: `ShadowModeType` (6 modes: observe_only/validation_required/canary_shadow/optimizer_shadow/promotion_shadow/production_drift_monitoring), `ShadowRunStatus`, `ShadowValidationStatus`, `ShadowRunRequest`, `ShadowRunManifest` (tracks total_divergences, threshold_exceedances, promotion_eligible).
- Notable: `required_passing_cycles: int = 5` on `ShadowRunRequest` shows the promotion gate requires sustained (not single-cycle) parity before a shadow-validated strategy can be promoted — consistent with the anti-flapping philosophy seen in the governance ladders.

### contracts/shadow/shadow_validation_summary.py (46 lines)
- Purpose: `ShadowValidationSummary` frozen dataclass — the top-level rollup of one shadow run, aggregating all 8 comparison-result lists plus `divergence_by_category` counts and the final `promotion_eligible` verdict.

### contracts/validators/core.py (184 lines)
- Purpose: The generic rule-engine framework: `Severity` enum (ERROR/WARNING), `Violation` (frozen dataclass: code/message/severity/field/context), `ValidationResult` (mutable accumulator with `.ok` property = no ERROR-severity violations), `ValidationContext` (frozen dataclass carrying run_id/prev-record/cross-object lookups), `Rule[T]` (frozen `Generic[T]` dataclass bundling a `check` predicate + lazy `message`/`context` callables + severity + field), `run_rules()` (applies a `Sequence[Rule[T]]` to an object and never raises — catches exceptions from a broken rule itself and converts them to an ERROR violation with code `VALIDATOR_EXCEPTION::<rule_code>`), plus shared predicates (`is_finite`, `is_non_negative`, `is_positive`, `is_ohlc_sane`, `is_aligned_to_minutes`, `is_strictly_increasing`).
- Notable: **Confirms the "contract-level validation as a rule engine" framing exactly.** This is a small, well-designed generic engine: rules are pure data (`Rule[T]` dataclasses holding lambdas), `run_rules` is the only executor, and it defensively converts a buggy rule's own exception into a normal ERROR violation rather than crashing the caller — a genuinely good defensive-programming pattern for a validation layer that will accumulate dozens of ad hoc rule files over time. WARNING-severity violations don't fail `ValidationResult.ok`, giving a two-tier hard/soft invariant model. This file is the true nucleus that every other `validators/*.py` file (17 of them) imports `Rule`/predicates from.

### contracts/validators/broker_order.py (63 lines)
- Purpose: `BROKER_ORDER_RULES` — 5 rules on `BrokerOrder` enforcing filled_qty non-negativity and exact FSM-consistency with `OrderStatus` (filled_qty==requested_qty when FILLED, strictly between 0 and requested_qty when PARTIALLY_FILLED).

### contracts/validators/cash_snapshot.py (37 lines)
- Purpose: `CASH_SNAPSHOT_RULES` — 4 rules on `CashSnapshot`: cash/buying_power/reserved_cash non-negative, and `reserved_cash <= cash + buying_power`.

### contracts/validators/corporate_action.py (77 lines)
- Purpose: `CORPORATE_ACTION_RULES` — 7 rules on `CorporateAction`: split_ratio required/positive/≠1.0 for split actions, new_symbol required for NAME_CHANGE, symbol/effective_date presence.
- Notable: **Code smell** — the `EFFECTIVE_DATE_PRESENT` rule (lines 65–70 and 71–76) is duplicated verbatim, back to back, in the `CORPORATE_ACTION_RULES` list — harmless (same check runs twice) but clearly an unintentional copy-paste artifact.

### contracts/validators/dataset_version.py (114 lines)
- Purpose: `DATASET_VERSION_RULES` — 9 rules on `DatasetVersion` including schema-version-per-dataset-name whitelisting (`SUPPORTED_SCHEMA_VERSIONS`) and conditional checksum/manifest requirements when `validation_status` is validated/complete/finalized.
- Notable: **Architecture/layering smell** — imports `RAW_BARS_DATASET`, `ADJUSTED_BARS_DATASET`, `CORPORATE_ACTIONS_DATASET` from `autonomous_trading_platform.storage.parquet.datasets`. Per CLAUDE.md's layering ("interfaces → application → domain → storage → contracts", contracts is the innermost layer), a contracts/ module importing from storage/ is a boundary violation — contracts should not depend on the storage layer at all, let alone the reverse of the intended dependency direction.

### contracts/validators/feature_dataset_version.py (157 lines)
- Purpose: `FEATURE_DATASET_VERSION_RULES` — 15 rules on `FeatureDatasetVersion` (schema-version whitelist restricted to `{"1.0"}`, `dataset_name` must literally equal "features", non-empty computation_parameters, finalized-status checksum/manifest requirements). Largest validator rule-set file.

### contracts/validators/fill.py (28 lines)
- Purpose: `FILL_RULES` — 3 rules on `Fill`: quantity/price positive, fees non-negative when present.

### contracts/validators/ingestion_run.py (80 lines)
- Purpose: `INGESTION_RUN_RULES` — 9 rules on `IngestionRun`: timestamp ordering (started_at≥created_at, completed_at≥started_at), completed-status requires completed_at, failed-status requires non-empty error_message.

### contracts/validators/market_bar.py (125 lines)
- Purpose: `MARKET_BAR_RULES` — 18 rules on `MarketBar`: 5-minute UTC boundary alignment, full OHLC sanity (high≥low, high≥max(o,c,l), low≤min(o,c,h), open/close within range), volume/trade_count non-negativity, `adjustment_factor` must be 1.0 when `price_basis=RAW`, and a hardcoded `INTERVAL_IS_FIVE_MIN` check.
- Notable: The `INTERVAL_IS_FIVE_MIN` rule hardcodes the platform to a single bar interval at the validator level even though `MarketBar.interval` and `BarInterval` support other granularities — suggests this rule set was written for (and is only valid for) the current 5-minute-bar deployment and would need updating if/when other intervals go live; a latent coupling between a "generic" validator and one specific operating configuration.

### contracts/validators/order_intent.py (58 lines)
- Purpose: `ORDER_INTENT_RULES` — 7 rules on `OrderIntent`: order-type-specific required fields (limit_price for LIMIT, stop_price for STOP, both for STOP_LIMIT), qty/notional exactly-one-of (confirms the earlier note that this mutual exclusion is validator-enforced, not model-enforced), extended_hours only allowed with LIMIT orders.

### contracts/validators/position_snapshot.py (22 lines)
- Purpose: `POSITION_SNAPSHOT_RULES` — 2 rules on `PositionSnapshot`: unique symbols across positions, all quantities non-negative (long-only mode assumption baked into the rule name/comment).

### contracts/validators/raw_market_symbol.py (67 lines)
- Purpose: `validate_raw_market_symbol()` / `validate_raw_market_pool_snapshot()` — plain functions returning a `RawMarketSymbolValidationResult` (mutable dataclass with `ok`/`errors`/`add_error`), checking symbol non-emptiness, uppercase/whitespace normalization, asset_type/status/source presence, first_seen≤last_seen.
- Notable: **Inconsistent with the rest of validators/** — every other file in this directory uses the `Rule[T]`/`run_rules` engine from `core.py`; this file instead hand-rolls its own imperative validator functions and its own bespoke result type, duplicating (in a less composable form) what `ValidationResult`/`Violation` already provide. Looks like it predates the `core.py` rule-engine or was written by someone unaware of it.

### contracts/validators/risk_snapshot.py (51 lines)
- Purpose: `RISK_SNAPSHOT_RULES` — 6 rules on `RiskSnapshot`: net/gross exposure and leverage finiteness, `gross_exposure >= abs(net_exposure)`, block_reasons required when is_blocked, leverage non-negative.

### contracts/validators/run_manifest.py (41 lines)
- Purpose: `RUN_MANIFEST_RULES` — 4 rules on `RunManifest`: capital_bucket positive, and BACKTEST run_type requires start/end date, random_seed, and both cost_model+fill_model — enforcing reproducibility fields are actually populated for backtests specifically.

### contracts/validators/signal.py (29 lines)
- Purpose: `SIGNAL_RULES` — 3 rules on `Signal`: bar_timestamp 5-minute alignment, FLAT direction requires target_position of 0/None, confidence in [0,1] when present.

### contracts/validators/ticker_lifecycle_event.py (47 lines)
- Purpose: `TICKER_LIFECYCLE_EVENT_RULES` — 4 rules on `TickerLifecycleEvent`: symbol/source non-empty, successor_symbol required for rename/merger/successor event types, successor_symbol must differ from symbol.

### contracts/validators/universe_snapshot.py (45 lines)
- Purpose: `UNIVERSE_SNAPSHOT_RULES` — 4 rules on the deprecated `UniverseSnapshot`: non-empty/unique symbols, effective window ordering, ticker regex format validation.
- Notable: File header explicitly states `# DEPRECATED: Use contracts/validators/universe_version.py instead` — consistent, honest deprecation labeling paired with `runtime/universe_snapshot.py`'s own deprecation note.

### contracts/validators/universe_version.py (82 lines)
- Purpose: `UNIVERSE_VERSION_RULES` (5 rules: status validity, effective window ordering, config_hash/name/source non-empty) + `UNIVERSE_MEMBER_RULES` (2 rules: ticker format, rank non-negative) + `is_valid_transition()` helper encoding the `UniverseStatus` state machine (`_VALID_STATUS_TRANSITIONS`: CANDIDATE→{PROPOSED,ACTIVE}, PROPOSED→{ACTIVE,RETIRED}, ACTIVE→{RETIRED}, RETIRED→{}).
- Notable: The only validators/ file that encodes an explicit state-transition graph as a first-class lookup table rather than a boolean predicate — real FSM validation logic (not just field-level checks), and it's exported as a plain function rather than wrapped in the `Rule[T]` framework since it takes two strings, not one contract instance.

---

## Standout candidates

- **contracts/runtime/platform_replay.py** (490 lines) — the largest file in the package by far: a full per-subsystem result/summary/timeline-event registry for the platform backtest/replay harness, spanning 16 domains with a custom recursive JSON-safe serializer (`PlatformBacktestArtifact.to_dict()`).
- **contracts/validators/core.py** — a compact, well-designed generic rule engine (`Rule[T]`, `ValidationContext`, `Violation`, `run_rules`) that 17 other validator files build on; defensively converts a broken rule's own exception into a normal ERROR violation rather than crashing.
- **contracts/execution/simulation_vs_paper_comparison.py** — real sim/live parity-testing domain logic (3-tier matching key priority, TWAP/VWAP slice aggregation) captured entirely as data contracts with an unusually thorough module docstring.
- **contracts/shadow/*** — a full shadow-mode/canary validation subsystem (8 comparison-result types × drift thresholds × promotion-eligibility gate with `required_passing_cycles`) mirroring the same 8-domain taxonomy used independently in platform_replay.py — evidence of a consistent mental model of the trading pipeline's phases across unrelated subsystems.
- **contracts/governance/drawdown_governance.py + strategy_health_lifecycle.py** — two independently-implemented but structurally identical anti-flapping governors (severity ladder + hysteresis band + per-rung cooldown + min-observation-cycles + allocation-scalar/penalty), showing a deliberate, repeated design pattern for introducing risk controls in observe-then-enforce stages.
- **contracts/common/types.py** — tiny (26 lines) but the single most-leveraged file in the package: `UTCDateTime` (Annotated datetime enforcing UTC at parse time) and `Money = Decimal` are used everywhere, eliminating whole classes of naive-datetime and float-money bugs at the type level.

## Gaps / smells

- **Architecture-layering violations** (contracts reaching outward, contradicting CLAUDE.md's "flow inward" rule and the "no business logic" framing):
  - `contracts/governance/strategy_governance.py` and `contracts/runtime/run_manifest.py` both import `GovernanceState` from `autonomous_trading_platform.governance.models.governance_state` — a non-contracts business-logic package.
  - `contracts/validators/dataset_version.py` imports dataset constants from `autonomous_trading_platform.storage.parquet.datasets` — contracts depending on storage/, backwards from the intended dependency direction.
- **"No business logic" is not fully true**: several contracts carry real behavior beyond data shape — `ExecutionPolicyConfig.model_validator` (mode-specific field enforcement) plus its `to_dict`/`from_dict`/factory classmethods; `DrawdownGovernanceLadderConfig.allocation_scalar_for()`/`cooldown_hours_for()` (match-based lookups); `RiskBudgetingRunResult.hidden_concentration_detected` (a concentration heuristic computed in a property); `PlatformReplayContext.create()` and `PlatformBacktestArtifact.to_dict()` (a hand-rolled recursive serializer). None of these are egregious, but collectively they show the "pure Pydantic data shapes" description is aspirational rather than strictly enforced.
- **Immutability is inconsistent and sometimes documented incorrectly**: only `simulation/dividend_event.py` is a `frozen=True` Pydantic BaseModel. `runtime/research_metrics_summary.py`'s docstring claims "Immutable after creation" but the class carries no `frozen` config — the immutability claim is asserted in prose only, not enforced by the type system. Roughly half the `@dataclass` usages (27 of 63) are also not frozen, so "immutability" varies file-by-file with no obvious rule for which mutable/frozen choice applies where.
- **Duplicated/overlapping contract families**, apparently from incremental migrations that didn't fully retire the old shape:
  - `runtime/live_metrics_summary.py` vs `runtime/live_performance_metrics.py` — near-identical field sets, the latter bolting on lineage fields "for backward compatibility" rather than the former being extended in place.
  - `runtime/metrics_summary.py` shows the same pattern for research-side metrics.
  - `runtime/optimizer.py` vs `runtime/optimization_backend.py` — two generations of MVO/optimizer contracts (different SolverStatus/FallbackMode member sets, different result shapes) coexisting.
  - `runtime/universe_snapshot.py` (+ its validator) is explicitly labeled `# DEPRECATED` in favor of `universe_version.py` — the one case where legacy debt is honestly documented rather than silently duplicated.
- **Copy-paste bug**: `contracts/validators/corporate_action.py` has the identical `EFFECTIVE_DATE_PRESENT` rule listed twice back-to-back in `CORPORATE_ACTION_RULES`.
- **Validator style inconsistency**: `contracts/validators/raw_market_symbol.py` is the only validator file that doesn't use the `Rule[T]`/`run_rules` engine from `core.py` — it hand-rolls its own imperative validation functions and a bespoke `RawMarketSymbolValidationResult` type instead.
- **Loosely-typed escape hatches**: `RiskSnapshot.limits`/`.utilization` (`dict[str, Any]`), and numerous `metadata: dict[str, Any]` fields across runtime/ contracts — pragmatic but weaken the type-level guarantees the rest of the package otherwise provides.
- No TODO/FIXME/XXX markers anywhere in contracts/ (confirmed via grep) — the package is either well-maintained or such markers are policy-excluded elsewhere.

## Coverage

Read 92 of 92 files (100%). The prior agent's 27 entries (10 `__init__.py` files described collectively + 17 per-file entries across accounting/, common/, market/, simulation/, trading/) were spot-checked: `contracts/common/types.py`, `contracts/trading/portfolio_signal.py`, and `contracts/simulation/dividend_event.py` were re-read in full during this session (their content matched the prior summaries exactly — UTCDateTime/Money aliasing, the SignalBatch→PortfolioSignal→SignalIntent pipeline, and the frozen dividend contract with field_validators, respectively) and found accurate. This session read and appended entries for all remaining 66 files: execution/ (5), governance/ (5), runtime/ (33 + confirming its `__init__.py` was already covered), shadow/ (5, including its non-empty `__init__.py`), and validators/ (18). No files were skipped.
