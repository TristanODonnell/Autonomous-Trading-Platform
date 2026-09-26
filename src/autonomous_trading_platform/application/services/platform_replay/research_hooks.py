"""Research domain replay hook (P1 — calendar-scheduled, not per-tick)."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    ResearchReplayResult,
    ResearchSummary,
)

logger = logging.getLogger(__name__)


def run_research_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    experiment_config,  # ExperimentDefinition — avoid hard import for optional dependency
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> ResearchReplayResult:
    """Run or resume a research experiment scoped to a timestamp window.

    Research is calendar-scheduled (weekly/monthly), not per-tick.
    The platform runner dispatches this on research_event days in the timeline.
    """
    base = dict(
        domain="research",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        return ResearchReplayResult(
            **base,
            status="dry_run",
            experiment_id=getattr(experiment_config, "experiment_id", None),
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    try:
        from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
            build_simulation_context,
        )

        simulation_context = build_simulation_context(session=session)
        results, filter_outputs = (
            simulation_context.experiment_orchestration_service.run_experiment(experiment_config)
        )
    except Exception as exc:
        return ResearchReplayResult(**base, status="failed", errors=[str(exc)])

    total_runs = len(results)
    passed = len([o for o in filter_outputs if o.filter_result.passed])

    return ResearchReplayResult(
        **base,
        status="ok",
        experiment_id=experiment_config.experiment_id,
        total_runs=total_runs,
        passed_filters=passed,
        summary={
            "experiment_id": experiment_config.experiment_id,
            "total_runs": total_runs,
            "passed_filters": passed,
            "timestamp": timestamp.isoformat(),
        },
    )


def run_scheduled_research_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dataset_version_id: str | None = None,
    research_options: dict | None = None,
) -> ResearchReplayResult:
    """Run the full staged research pipeline at a scheduled replay timestamp.

    Called monthly during platform replay. Builds an experiment with a staged
    pipeline (quality filter → walk-forward → Monte Carlo → regime → stress →
    overfitting), runs it, runs the validation + intelligence layer on final
    survivors, and seeds StrategyGovernance records for deployable strategies
    in ranked order.

    research_options comes from the fixture's scheduled_jobs.research.options.
    Supported keys:
      profile: "full" (default) | "smoke" — smoke shrinks candidate counts,
               Monte Carlo runs and cost scenarios for cheap verification runs.
    """
    profile = _resolve_research_profile(research_options)
    base = dict(
        domain="research",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if replay_context.dry_run:
        return ResearchReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    # Resolve active universe symbols before building simulation context so the
    # position sizer can allocate capital correctly across all active symbols.
    _universe_size = _resolve_active_universe_size(
        session=session, timestamp=timestamp, replay_context=replay_context
    )

    try:
        from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
            build_simulation_context,
        )

        simulation_context = build_simulation_context(
            session=session, universe_size=_universe_size, lookback_bars=20
        )
    except Exception as exc:
        return ResearchReplayResult(**base, status="failed", errors=[str(exc)])

    from autonomous_trading_platform.universe.services.survivorship_guard import (
        SurvivorshipBiasError,
    )

    try:
        experiment_def = _build_replay_experiment_definition(
            session=session,
            timestamp=timestamp,
            replay_context=replay_context,
            simulation_runner=simulation_context.simulation_runner,
            dataset_version_id_override=dataset_version_id,
            profile=profile,
        )
    except SurvivorshipBiasError as exc:
        return ResearchReplayResult(**base, status="failed", errors=[f"survivorship_guard: {exc}"])
    if experiment_def is None:
        return ResearchReplayResult(
            **base,
            status="skipped",
            summary={
                "reason": "no_validated_dataset",
                "timestamp": timestamp.isoformat(),
            },
            warnings=["Research skipped — no validated adjusted_bars or raw_bars dataset found"],
        )

    try:
        pipeline_result = simulation_context.experiment_orchestration_service.run_staged_experiment(
            experiment_def
        )
    except Exception as exc:
        return ResearchReplayResult(**base, status="failed", errors=[str(exc)])

    total_runs = len(pipeline_result.all_simulation_results)
    stage_results = pipeline_result.stage_results
    stage1_count = stage_results[0].n_passed if len(stage_results) > 0 else 0
    stage2_count = stage_results[1].n_passed if len(stage_results) > 1 else 0
    stage3_count = stage_results[2].n_passed if len(stage_results) > 2 else 0
    stage_funnel = {
        sr.stage_name: {"entered": sr.n_entered, "passed": sr.n_passed} for sr in stage_results
    }

    final_survivors = pipeline_result.final_survivors
    config_by_id = {c.strategy_id: c for c in final_survivors}

    # First sim result per strategy (from earliest stage it appeared — used for equity curve)
    sim_by_id: dict = {}
    for r in pipeline_result.all_simulation_results:
        if r.strategy_id not in sim_by_id:
            sim_by_id[r.strategy_id] = r

    # Phase 2 — validation + intelligence pipeline on Stage 3 survivors
    intelligence_summaries: list = []
    intel_svc = None
    try:
        from autonomous_trading_platform.research.intelligence.research_intelligence_service import (
            ResearchIntelligenceRequest,
            ResearchIntelligenceService,
        )
        from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
            collect_overfitting_evidence,
        )
        from autonomous_trading_platform.research.validation.survivorship_validation import (
            SurvivorshipValidationService,
        )
        from autonomous_trading_platform.research.validation.validation_orchestrator import (
            ValidationOrchestrator,
            ValidationRequest,
        )
        from autonomous_trading_platform.universe.services.survivorship_guard import (
            SurvivorshipGuard,
        )

        val_orchestrator = ValidationOrchestrator(
            survivorship_service=SurvivorshipValidationService(
                survivorship_guard=SurvivorshipGuard()
            )
        )
        universe_scope = _resolve_research_universe_scope(
            session=session,
            start_date=experiment_def.start_date,
            end_date=experiment_def.end_date,
        )
        intel_svc = ResearchIntelligenceService()

        for config in final_survivors:
            sid = config.strategy_id
            # Evidence the robustness stages recorded (fold pairs, MC dispersion,
            # regime profile, full-window reference run) feeds the validation
            # layer, so its overfitting/regime checks see the same data as the gates.
            evidence = collect_overfitting_evidence(sid, stage_results)
            sim_result = evidence.reference_result or sim_by_id.get(sid)
            if sim_result is None:
                continue
            equity_curve = sim_result.equity_curve
            if equity_curve is None or equity_curve.empty:
                continue
            try:
                val_summary = val_orchestrator.run_validation(
                    ValidationRequest(
                        strategy_id=sid,
                        experiment_id=experiment_def.experiment_id,
                        dataset_version=experiment_def.dataset_version,
                        equity_curve=equity_curve,
                        trade_count=sim_result.trade_count,
                        wf_fold_inputs=evidence.fold_inputs or None,
                        mc_aggregation=evidence.mc_aggregation,
                        regime_profile=evidence.regime_profile,
                        universe_scope=universe_scope,
                        experiment_start=experiment_def.start_date,
                        experiment_end=experiment_def.end_date,
                    )
                )
                intel_summary = intel_svc.analyze(
                    ResearchIntelligenceRequest(
                        strategy_id=sid,
                        experiment_id=experiment_def.experiment_id,
                        dataset_version=experiment_def.dataset_version,
                        run_id=str(sim_result.run_id),
                        validation_summary=val_summary,
                        persist=False,
                    )
                )
                intelligence_summaries.append(intel_summary)
            except Exception:
                logger.warning("Intelligence analysis failed for strategy %s", sid, exc_info=True)
    except Exception as exc:
        logger.warning("Intelligence pipeline setup failed: %s", exc)

    # Rank, cluster, and analyze regime diversity
    ranked: list = []
    clusters: list = []
    regime_diversity: dict = {}
    if intelligence_summaries and intel_svc is not None:
        try:
            ranked = intel_svc.rank_candidates(intelligence_summaries)
            clusters = intel_svc.cluster_candidates(intelligence_summaries)
            regime_diversity = intel_svc.analyze_regime_diversity(intelligence_summaries)
        except Exception as exc:
            logger.warning("Intelligence ranking/clustering failed: %s", exc)

    top_strategy_id = None
    top_score = 0.0
    if ranked:
        _, top_summary = ranked[0]
        top_strategy_id = top_summary.strategy_id
        top_score = top_summary.candidate_score.composite_score

    spam_cluster_count = sum(1 for c in clusters if c.is_parameter_spam)
    diversity_score = regime_diversity.get("diversification_score", 0.0)

    logger.info(
        "research_tick_complete | profile=%s | generated=%d | funnel=%s | "
        "validated=%d | top=%s score=%.3f | clusters=%d spam=%d | regime_diversity=%.3f",
        profile,
        total_runs,
        " -> ".join(f"{name}:{c['passed']}/{c['entered']}" for name, c in stage_funnel.items()),
        len(intelligence_summaries),
        top_strategy_id or "none",
        top_score,
        len(clusters),
        spam_cluster_count,
        diversity_score,
    )

    # De-duplicate parameter-spam clusters before seeding governance.
    # For each spam cluster, keep only the highest-ranked member and drop the rest.
    # Non-spam clusters and unclustered strategies are not restricted.
    rank_position: dict[str, int] = {
        summary.strategy_id: i for i, (_, summary) in enumerate(ranked)
    }
    spam_excluded: set[str] = set()
    for cluster in clusters:
        if not cluster.is_parameter_spam:
            continue
        members = [m.strategy_id for m in cluster.members if m.strategy_id in rank_position]
        if len(members) <= 1:
            continue
        best = min(members, key=lambda sid: rank_position.get(sid, 999))
        spam_excluded.update(sid for sid in members if sid != best)

    deployable = [
        s
        for _, s in ranked
        if s.candidate_score.is_deployable and s.strategy_id not in spam_excluded
    ]
    _seed_research_governance_from_intelligence(
        session=session,
        summaries=deployable,
        config_by_id=config_by_id,
        sim_by_id=sim_by_id,
        experiment_id=experiment_def.experiment_id,
        now_utc=timestamp,
    )

    return ResearchReplayResult(
        **base,
        status="ok",
        experiment_id=experiment_def.experiment_id,
        total_runs=total_runs,
        passed_filters=len(final_survivors),
        summary={
            "experiment_id": experiment_def.experiment_id,
            "total_runs": total_runs,
            "stage_1_survivors": stage1_count,
            "stage_2_survivors": stage2_count,
            "stage_3_survivors": stage3_count,
            "stage_funnel": stage_funnel,
            "research_profile": profile,
            "intelligence_analyzed": len(intelligence_summaries),
            "deployable_seeded": len(deployable),
            "spam_cluster_excluded": len(spam_excluded),
            "top_strategy_id": top_strategy_id,
            "top_composite_score": top_score,
            "cluster_count": len(clusters),
            "regime_diversity_score": diversity_score,
            "timestamp": timestamp.isoformat(),
        },
    )


def _resolve_active_universe_size(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> int:
    """Return the number of symbols in the active universe at timestamp.

    Falls back to len(replay_context.symbols) if universe resolution fails.
    """
    try:
        from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
            UniverseVersionRepository,
        )
        from autonomous_trading_platform.universe.services.universe_resolution_service import (
            UniverseResolutionService,
        )

        _uvr = UniverseVersionRepository(session)
        resolver = UniverseResolutionService(_uvr)
        active = resolver.resolve_active(timestamp)
        if active is not None:
            members = _uvr.get_included_members(active.universe_version_id)
            active_symbols = [m.symbol for m in members]
            if active_symbols:
                return len(active_symbols)
    except Exception:
        pass
    return max(1, len(replay_context.symbols))


def _build_replay_experiment_definition(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    simulation_runner,  # SimulationRunner — avoid hard import cycle
    dataset_version_id_override: str | None = None,
    profile: str = "full",
):
    """Build an ExperimentDefinition with the staged pipeline for the monthly research tick.

    Stage 1 always runs. Walk-forward and everything after it (Monte Carlo,
    regime, stress, overfitting) need >= 75 days of data.

    Returns None if no validated dataset is available.
    """
    from autonomous_trading_platform.common.annualisation import BARS_PER_DAY
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from autonomous_trading_platform.research.experiments.filtering.config import (
        FilterConfig,
        ScoringWeights,
    )
    from autonomous_trading_platform.research.experiments.models.experiment_plan import (
        ExperimentDefinition,
        ExperimentType,
    )
    from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
        OverfittingGateConfig,
    )
    from autonomous_trading_platform.research.pipeline.gates.regime_gate import RegimeGateConfig
    from autonomous_trading_platform.research.pipeline.gates.regime_labels import (
        OnTheFlyRegimeLabelProvider,
    )
    from autonomous_trading_platform.research.pipeline.gates.stress_gate import StressGateConfig
    from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
    from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
        MonteCarloStage,
        MonteCarloStageConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.overfitting_stage import (
        OverfittingStage,
        OverfittingStageConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.regime_stage import (
        RegimeStage,
        RegimeStageConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
        SimulationStage,
        SimulationStageConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.stress_stage import (
        StressStage,
        StressStageConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
        WalkForwardStage,
        WalkForwardStageConfig,
    )
    from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

    smoke = profile == "smoke"
    tick_date = timestamp.date()

    # When running inside a platform backtest, use the pre-created cumulative version
    # directly to avoid picking up stale validated versions from prior runs.
    if dataset_version_id_override is not None:
        dataset_version = dataset_version_id_override
        price_basis = PriceBasis.RAW
    else:
        # Resolve dataset_version from latest validated adjusted_bars, then raw_bars
        dataset_row = (
            session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == "adjusted_bars")
            .filter(DatasetVersions.validation_status == "validated")
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )
        if dataset_row is None:
            dataset_row = (
                session.query(DatasetVersions)
                .filter(DatasetVersions.dataset_name == "raw_bars")
                .filter(DatasetVersions.validation_status == "validated")
                .order_by(DatasetVersions.created_at.desc())
                .first()
            )
        if dataset_row is None:
            return None

        dataset_version = dataset_row.dataset_version_id
        price_basis = (
            PriceBasis.ADJUSTED if dataset_row.dataset_name == "adjusted_bars" else PriceBasis.RAW
        )

    lookback_days = (
        90  # ~64 trading days; covers 3 months so walk-forward (needs 75 days) always fires
    )
    lookback_start = tick_date - timedelta(days=lookback_days)
    # Clamp to the dataset's coverage start so walk-forward folds don't request
    # data before the replay began (cumulative dataset has no pre-replay bars).
    replay_start: date | None = None
    if dataset_version_id_override is not None:
        try:
            from autonomous_trading_platform.storage.sor.models.dataset_versions import (
                DatasetVersions,
            )

            dv_row = (
                session.query(DatasetVersions)
                .filter(DatasetVersions.dataset_version_id == dataset_version_id_override)
                .first()
            )
            if dv_row is not None:
                replay_start = dv_row.date_coverage_start
        except Exception:
            pass
    start_date = max(lookback_start, replay_start) if replay_start else lookback_start

    available_days = (tick_date - start_date).days

    # Need at least 15 calendar days for stage 1 to be meaningful.
    # Guards against monthly cadence firing on day 1 (last=None → True).
    if available_days < 15:
        return None

    # Survivorship-safe research universe: members active at the START of the
    # research window, not at tick_date. A tick-date universe silently drops
    # names that died inside the window; anchoring at the window start keeps
    # them (their bars simply stop). SurvivorshipGuard enforces the anchor.
    scope = _resolve_research_universe_scope(
        session=session, start_date=start_date, end_date=tick_date
    )
    universe_version = scope.universe_version_id or "v1"
    symbols = sorted(scope.member_symbols)
    if dataset_version_id_override is not None:
        # Backtest mode: only symbols the replay ingested have Parquet bars.
        _ingested = set(replay_context.symbols)
        symbols = [s for s in symbols if s in _ingested]
    if not symbols:
        from autonomous_trading_platform.universe.services.survivorship_guard import (
            SurvivorshipBiasError,
        )

        raise SurvivorshipBiasError(
            f"Universe active on {start_date} ({scope.universe_version_id}) has no "
            "ingested symbols — refusing to fall back to an unanchored symbol list."
        )

    # Use half of available CPUs for parallel simulation (threads share the GIL
    # but I/O — parquet reads — runs concurrently, giving real speedup there).
    _cpu = os.cpu_count() or 1
    _sim_workers = max(2, _cpu // 2)
    experiment_id = f"replay_research_{tick_date.strftime('%Y%m')}"

    default_weights = ScoringWeights()

    # Tighter filter shared by walk-forward and Monte Carlo stages
    wf_mc_filter = FilterConfig(
        min_sharpe=0.3,
        max_drawdown=-0.35,
        min_trades=5,
        min_consistency_score=0.3,
        min_total_return=-0.05,
    )

    # Stage 1 — always runs (just needs start_date < end_date)
    stage1 = SimulationStage(
        stage_config=SimulationStageConfig(
            name="initial_quality_filter",
            start_date=start_date,
            end_date=tick_date,
            symbols=symbols,
            filter_config=FilterConfig(
                min_sharpe=0.5,
                max_drawdown=-0.30,
                min_trades=10,
                min_consistency_score=0.4,
                min_total_return=0.0,
            ),
            scoring_weights=default_weights,
            max_workers=1,
        ),
        simulation_runner=simulation_runner,
    )

    stages: list = [stage1]

    # Stage 2 — WalkForwardStage requires train_days + test_days of data (75 days).
    # With a 42-day lookback this rarely triggers until month 3+; survivors from
    # stage 1 are already filtered so the fold simulations are cheap.
    _wf_train_days, _wf_test_days = 45, 30
    if available_days >= _wf_train_days + _wf_test_days:
        stage2 = WalkForwardStage(
            stage_config=WalkForwardStageConfig(
                name="walk_forward_robustness",
                symbols=symbols,
                start_date=start_date,
                end_date=tick_date,
                train_days=_wf_train_days,
                test_days=_wf_test_days,
                step_days=15,
                train_filter_config=wf_mc_filter,
                train_scoring_weights=default_weights,
                test_filter_config=wf_mc_filter,
                test_scoring_weights=default_weights,
                require_all_folds=False,
                min_folds_passed=1,
                max_workers=1,
            ),
            simulation_runner=simulation_runner,
        )
        stages.append(stage2)

        # Stage 3 — MonteCarloStage: only survivors of walk-forward enter (typically 2-5).
        stage3 = MonteCarloStage(
            stage_config=MonteCarloStageConfig(
                name="monte_carlo_structural_robustness",
                symbols=symbols,
                start_date=start_date,
                end_date=tick_date,
                n_runs=3 if smoke else 5,
                min_pass_rate=0.6,
                filter_config=wf_mc_filter,
                scoring_weights=default_weights,
                max_workers=1,
            ),
            simulation_runner=simulation_runner,
        )
        stages.append(stage3)

        # Stage 4 — regime robustness on auto-classified daily regimes. Reuses
        # the Monte Carlo representative run (same window), so it adds no
        # simulations: one bar read + classification per research tick.
        stages.append(
            RegimeStage(
                stage_config=RegimeStageConfig(
                    name="regime_robustness",
                    symbols=symbols,
                    start_date=start_date,
                    end_date=tick_date,
                    gate=RegimeGateConfig(
                        dimensions=("trend", "volatility"),
                        # Research sims run on 5-min bars: a regime needs ~5
                        # trading days of bars before its metrics mean anything.
                        min_bars_per_regime=5 * BARS_PER_DAY,
                        min_regime_sharpe=-1.0,
                        max_regime_drawdown=-0.20,
                        min_positive_regime_fraction=0.5,
                        on_insufficient_coverage="pass",
                    ),
                ),
                simulation_runner=simulation_runner,
                label_provider=OnTheFlyRegimeLabelProvider.from_simulation_runner(
                    simulation_runner
                ),
            )
        )

        # Stage 5 — stress: equity-curve shocks (free) + execution-cost re-runs.
        stages.append(
            StressStage(
                stage_config=StressStageConfig(
                    name="stress_robustness",
                    symbols=symbols,
                    start_date=start_date,
                    end_date=tick_date,
                    gate=StressGateConfig(
                        min_shock_sharpe=0.0,
                        max_shock_drawdown=-0.40,
                        min_shock_survival_rate=0.5,
                        cost_multipliers=(2.0,) if smoke else (2.0, 3.0),
                        min_cost_sharpe=0.0,
                        max_cost_drawdown=-0.40,
                        min_cost_survival_rate=0.5,
                    ),
                ),
                simulation_runner=simulation_runner,
            )
        )

        # Stage 6 — overfitting: no simulations; combines WF/MC/regime evidence.
        stages.append(
            OverfittingStage(
                stage_config=OverfittingStageConfig(
                    name="overfitting_gate",
                    gate=OverfittingGateConfig(
                        max_overfitting_probability=0.6,
                        min_core_indicators=2,
                        min_trade_count=10,
                        on_insufficient_evidence="pass",
                    ),
                )
            )
        )

    return ExperimentDefinition(
        experiment_id=experiment_id,
        experiment_type=ExperimentType.SWEEP,
        description=f"Monthly platform replay research — {tick_date.strftime('%B %Y')}",
        strategy_set=_replay_strategy_set(smoke=smoke),
        parameter_grid=[{}],
        dataset_version=dataset_version,
        universe_version=universe_version,
        price_basis=price_basis,
        symbols=symbols,
        start_date=start_date,
        end_date=tick_date,
        random_seed=int(tick_date.strftime("%Y%m")),
        parameter_space={},
        staged_pipeline_config=StagedPipelineConfig(stages=stages),
    )


def _resolve_research_universe_scope(*, session: Session, start_date: date, end_date: date):
    """Point-in-time universe scope for a research window, validated by SurvivorshipGuard.

    Raises SurvivorshipBiasError when no universe was active at start_date.
    """
    from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
        UniverseRotationRepository,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
        UniverseVersionRepository,
    )
    from autonomous_trading_platform.universe.services.experiment_universe_resolver import (
        ExperimentUniverseResolver,
    )
    from autonomous_trading_platform.universe.services.survivorship_guard import (
        SurvivorshipGuard,
    )
    from autonomous_trading_platform.universe.services.universe_history_service import (
        UniverseHistoryService,
    )

    version_repo = UniverseVersionRepository(session)
    resolver = ExperimentUniverseResolver(
        history_service=UniverseHistoryService(
            version_repo=version_repo, rotation_repo=UniverseRotationRepository(session)
        ),
        version_repo=version_repo,
    )
    scope = resolver.resolve_active_as_of(start_date)
    SurvivorshipGuard().validate_experiment_scope(scope, start_date, end_date)
    return scope


_RESEARCH_PROFILES = frozenset({"full", "smoke"})


def _resolve_research_profile(research_options: dict | None) -> str:
    profile = str((research_options or {}).get("profile", "full"))
    if profile not in _RESEARCH_PROFILES:
        logger.warning(
            "Unknown research profile %r — falling back to 'full' (valid: %s)",
            profile,
            sorted(_RESEARCH_PROFILES),
        )
        return "full"
    return profile


def _replay_strategy_set(*, smoke: bool) -> list[dict]:
    """Candidate mix per research tick. Smoke keeps every family at 2 samples each."""
    full_counts = {
        "momentum": 6,
        "mean_reversion": 6,
        "moving_average_crossover": 6,
        "factor_based": 8,
        "composite_rule": 10,
    }
    return [
        {"type": t, "method": "random", "options": {"n_samples": 2 if smoke else n}}
        for t, n in full_counts.items()
    ]


def _seed_research_governance_from_intelligence(
    *,
    session: Session,
    summaries: list,  # list[ResearchIntelligenceSummary]
    config_by_id: dict,  # strategy_id -> StrategyConfig
    sim_by_id: dict,  # strategy_id -> SimulationRunResult
    experiment_id: str,
    now_utc: datetime,
) -> None:
    """Upsert StrategyGovernance rows for deployable intelligence survivors."""
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    for summary in summaries:
        strategy_id = summary.strategy_id
        config = config_by_id.get(strategy_id)
        config_hash = (
            config.config_hash()
            if config is not None
            else hashlib.sha256(strategy_id.encode()).hexdigest()[:16]
        )
        sim_result = sim_by_id.get(strategy_id)
        source_run_id = str(sim_result.run_id) if sim_result is not None else None
        existing = session.get(StrategyGovernance, (strategy_id, config_hash))
        if existing is None:
            session.add(
                StrategyGovernance(
                    strategy_id=strategy_id,
                    config_hash=config_hash,
                    current_state="approved_research",
                    experiment_id=experiment_id,
                    source_run_id=source_run_id,
                    submitted_at=now_utc,
                    updated_at=now_utc,
                    submitted_by="system",
                )
            )
        elif existing.source_run_id is None and source_run_id is not None:
            # Back-fill source_run_id on rows seeded by a previous run that lacked it.
            existing.source_run_id = source_run_id
            existing.updated_at = now_utc
    try:
        session.flush()
    except Exception:
        logger.exception("_seed_strategy_promotions: flush failed — rolling back")
        session.rollback()
        raise


def _seed_research_governance(
    *,
    session: Session,
    survivors,  # list[FilterScoreOutput]
    experiment_id: str,
    now_utc: datetime,
) -> None:
    """Upsert StrategyGovernance rows for research survivors in approved_research state."""
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    for output in survivors:
        strategy_id = output.strategy_id
        config_hash = hashlib.sha256(strategy_id.encode()).hexdigest()[:16]
        existing = session.get(StrategyGovernance, (strategy_id, config_hash))
        if existing is None:
            session.add(
                StrategyGovernance(
                    strategy_id=strategy_id,
                    config_hash=config_hash,
                    current_state="approved_research",
                    experiment_id=experiment_id,
                    source_run_id=None,
                    submitted_at=now_utc,
                    updated_at=now_utc,
                    submitted_by="system",
                )
            )
    try:
        session.flush()
    except Exception:
        logger.exception("_seed_research_governance: flush failed — rolling back")
        session.rollback()
        raise


def build_research_summary(*, session: Session) -> ResearchSummary:
    """Read latest research state for the platform artifact bundle."""
    try:
        svc = ExperimentCatalogService(session=session)
        rows = svc.list_experiments()
        if not rows:
            return ResearchSummary(
                experiment_id=None, total_runs=0, passed_filters=0, run_timestamp=None
            )
        latest = rows[0]
        return ResearchSummary(
            experiment_id=latest.get("experiment_name"),
            total_runs=latest.get("total_strategies", 0) or 0,
            passed_filters=latest.get("strategies_passed_filters", 0) or 0,
            run_timestamp=str(latest.get("created_at", "")),
        )
    except Exception:
        return ResearchSummary(
            experiment_id=None, total_runs=0, passed_filters=0, run_timestamp=None
        )
