"""Research domain replay hook (P1 — calendar-scheduled, not per-tick)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    ResearchReplayResult,
    ResearchSummary,
)
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    ResearchWindowDataset,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import UniverseTimeline
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

if TYPE_CHECKING:
    from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
    from autonomous_trading_platform.research.pipeline.admission_window import (
        AdmissionWindowLayout,
        SessionSpan,
    )
    from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunResult,
    )

# ``get_logger``, as the simulation runner uses: a logger from ``logging.getLogger``
# has no handler in a job's process and its INFO lines are dropped.
logger = get_logger(__name__)


# Research rejects candidates that trade more than this many times their capital per day
# (rotation step 5c-G: 5-minute noise traders turned over 13-26x a day; the strategies
# that held positions ran ~2x). Slippage is already in research returns; this cap is a
# backstop for edges too thin to survive live costs.
RESEARCH_MAX_DAILY_TURNOVER = 10.0

# Calendar days of history a research tick reads (about 64 sessions). Override
# per replay with research.options.lookback_days. Walk-forward needs 75.
DEFAULT_RESEARCH_LOOKBACK_DAYS = 90

# Fewer calendar days than this and stage 1 has too little to judge.
MIN_RESEARCH_WINDOW_DAYS = 15

# research.options.window_layout. Left out, the window is ``lookback_days`` long and
# walk-forward rolls its folds through it. "admission" lays the window out from the
# holdout date instead: selection, evaluation and holdout
# (research/pipeline/admission_window.py).
WINDOW_LAYOUT_ADMISSION = "admission"
_WINDOW_LAYOUTS = frozenset({WINDOW_LAYOUT_ADMISSION})

# The lookback window's walk-forward folds, in calendar days: it needs train + test
# (75 days) of window, and the stages after it are built only with it.
_ROLLING_TRAIN_DAYS = 45
_ROLLING_TEST_DAYS = 30
_ROLLING_FOLD_LAYOUT: Mapping[str, Any] = {
    "train_days": _ROLLING_TRAIN_DAYS,
    "test_days": _ROLLING_TEST_DAYS,
    "step_days": 15,
    # Known exception (plan R2 / R6): on a 90-day window the two test folds share
    # 15 days, and a layout without overlap leaves one fold. The admission layout
    # has no such exception.
    "allow_overlapping_test_folds": True,
    # Known exception as well: 30-day test folds are under the 84-day minimum, and a
    # 90-day window cannot hold longer ones. The admission layout's folds clear it.
    "allow_short_test_folds": True,
    "min_folds_passed": 1,
}


class ResearchWindowError(ValueError):
    """The window a research job asked for cannot be laid on the dataset it named.
    The research step fails with the reason; the window is not shortened to fit.
    """


@dataclass(frozen=True)
class _ResearchWindow:
    """Which days each stage of one research tick reads. Every date named is read.

    start_date, end_date   the experiment's window; walk-forward's folds lie inside it
    selection_end          stage 1 reads start_date to selection_end
    gates_start            Monte Carlo, regime and stress read gates_start to end_date
    fold_layout            how walk-forward lays out its folds, as arguments of
                           WalkForwardStageConfig; None when the window is too short
                           for walk-forward, and stage 1 is then the only stage
    gate_run_clears_filter True = the one run over gates_start to end_date (Monte
                           Carlo's base run) must itself clear the walk-forward
                           filter, besides its resampled paths
    neighbour_check        True = after stress, each survivor is re-run with one
                           setting moved a little, each way, over gates_start to
                           end_date (plan R13; report only)
    """

    start_date: date
    end_date: date
    selection_end: date
    gates_start: date
    fold_layout: Mapping[str, Any] | None
    gate_run_clears_filter: bool = False
    neighbour_check: bool = False


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
    reuse_completed_runs: bool = False,
) -> ResearchReplayResult:
    """Run the full staged research pipeline at a scheduled replay timestamp.

    Called monthly during platform replay. Builds an experiment with a staged
    pipeline (quality filter → walk-forward → Monte Carlo → regime → stress →
    overfitting), runs it, runs the validation + intelligence layer on final
    survivors, and seeds StrategyGovernance records for deployable strategies
    in ranked order.

    reuse_completed_runs: a simulation this experiment already completed, on this
    database and these artifacts, by this code, is read back and not run again
    (``research/simulation/completed_run_reuse.py``, plan R3). For a job resumed
    after it stopped partway; the step's summary says how many were read back.
    Every stage judges what it gets back again, so the outcome is the one an
    uninterrupted run gives. The holdout runs leave no row and are always run.

    research_options comes from the fixture's scheduled_jobs.research.options.
    Supported keys:
      profile: "full" (default) | "smoke" — smoke shrinks candidate counts,
               Monte Carlo runs and cost scenarios for cheap verification runs.
      lookback_days: calendar days of history the research window covers
               (default 90). The window is still clamped to the dataset's start.
      universe_timeline: dataset version id of an exported universe timeline
               (``atp universe export-timeline``). With it the universe follows
               the timeline month by month, as on the trading platform, and the
               universe tables are not read. Without it the universe is the
               list active on the window's first day, for the whole window.
      dataset_version: dataset version id of a research-window dataset
               (``atp datasets build-window``). With it research reads those
               bars in place of the dataset the replay hands it: the window
               ends on the tick's date or the dataset's last day, whichever
               is earlier, names are checked against the names the dataset
               holds, and a window that would start before its first day is
               a failed step, not a shorter window.
      window_layout: "admission" lays the window out from the holdout date
               (``research/pipeline/holdout.py``) in place of ``lookback_days``:
               stage 1 reads the selection piece; walk-forward tests on the
               evaluation piece cut into three folds, every fold's train
               window being the selection piece, two of three to pass; Monte
               Carlo, regime and stress read the evaluation piece, and the
               one run over it must itself clear the walk-forward filter; no
               stage reads the holdout, and the pipeline is refused if one
               would. The final survivors are then each run once over the
               holdout sessions there are so far and the numbers reported in
               the summary (``holdout_report``); nobody is eliminated by them
               and the runs leave no row in the database. It needs
               ``universe_timeline`` and ``dataset_version`` and refuses
               ``lookback_days``. A dataset the layout cannot be laid on is a
               failed step. Any other value is an error.

    Under either layout the summary's ``gate_report`` holds what every stage said
    about every strategy (``research/pipeline/gate_report.py``): the funnel gives the
    counts, and a report-only gate's findings show in no count.
    """
    profile = _resolve_research_profile(research_options)
    lookback_days = _resolve_lookback_days(research_options)
    universe_timeline_id = _resolve_universe_timeline_option(research_options)
    research_dataset_id = _resolve_dataset_version_option(research_options)
    window_layout = _resolve_window_layout_option(research_options)
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

    # The timeline is read once, here, and checked against its registered hash;
    # the runner asks the same source for it and gets the copy already read.
    timeline_source = _universe_timeline_source(session)
    universe_timeline: UniverseTimeline | None = None
    if universe_timeline_id is not None:
        try:
            universe_timeline = timeline_source(universe_timeline_id)
        except Exception as exc:
            return ResearchReplayResult(
                **base, status="failed", errors=[f"universe_timeline: {exc}"]
            )

    # A dataset the job named is looked up before anything is built: what it is,
    # where its bars begin and end, and the names it holds.
    research_dataset: ResearchWindowDataset | None = None
    if research_dataset_id is not None:
        from autonomous_trading_platform.application.services.research_window_dataset_loader import (
            load_research_window_dataset,
        )

        try:
            research_dataset = load_research_window_dataset(session, research_dataset_id)
        except Exception as exc:
            return ResearchReplayResult(**base, status="failed", errors=[f"dataset_version: {exc}"])

    # The admission layout is laid on the named dataset before anything is built: a
    # dataset it does not fit fails the step here, with the reason.
    admission_layout: AdmissionWindowLayout | None = None
    if window_layout == WINDOW_LAYOUT_ADMISSION:
        if research_dataset is None:
            # Not reachable through the options: the layout is refused without a dataset.
            raise ValueError("the admission layout was asked for without a dataset")
        try:
            admission_layout = _lay_out_admission_window(
                tick_date=timestamp.date(), research_dataset=research_dataset
            )
        except ResearchWindowError as exc:
            return ResearchReplayResult(**base, status="failed", errors=[f"research_window: {exc}"])

    try:
        from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
            build_simulation_context,
        )

        simulation_context = build_simulation_context(
            session=session,
            universe_size=_universe_size,
            universe_timeline_source=timeline_source,
        )
    except Exception as exc:
        return ResearchReplayResult(**base, status="failed", errors=[str(exc)])

    if reuse_completed_runs:
        simulation_context.simulation_runner.reuse_completed_runs()

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
            lookback_days=lookback_days,
            universe_timeline=universe_timeline,
            research_dataset=research_dataset,
            admission_layout=admission_layout,
        )
    except SurvivorshipBiasError as exc:
        return ResearchReplayResult(**base, status="failed", errors=[f"survivorship_guard: {exc}"])
    except ResearchWindowError as exc:
        return ResearchReplayResult(**base, status="failed", errors=[f"research_window: {exc}"])
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
    gate_report = _report_gates(stage_results)

    final_survivors = pipeline_result.final_survivors
    config_by_id = {c.strategy_id: c for c in final_survivors}

    # First sim result per strategy (from earliest stage it appeared — used for equity curve)
    sim_by_id: dict = {}
    for r in pipeline_result.all_simulation_results:
        if r.strategy_id not in sim_by_id:
            sim_by_id[r.strategy_id] = r

    # The run a governance record points at, which promotion reads its metrics
    # from: the full-window reference run when the robustness stages recorded
    # one, stage 1's run otherwise. Stage 1 covers the selection window only
    # once walk-forward follows it, so its run is not the whole-window result.
    source_sim_by_id: dict = dict(sim_by_id)

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
        universe_scope = (
            _timeline_universe_scope(
                universe_timeline, experiment_def.start_date, experiment_def.end_date
            )
            if universe_timeline is not None
            else _resolve_research_universe_scope(
                session=session,
                start_date=experiment_def.start_date,
                end_date=experiment_def.end_date,
            )
        )
        intel_svc = ResearchIntelligenceService()

        for config in final_survivors:
            sid = config.strategy_id
            # Evidence the robustness stages recorded (fold pairs, MC resampling,
            # regime profile, full-window reference run) feeds the validation
            # layer, so its overfitting/regime checks see the same data as the gates.
            evidence = collect_overfitting_evidence(sid, stage_results)
            sim_result = evidence.reference_result or sim_by_id.get(sid)
            if sim_result is None:
                continue
            source_sim_by_id[sid] = sim_result
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
        sim_by_id=source_sim_by_id,
        experiment_id=experiment_def.experiment_id,
        now_utc=timestamp,
    )

    # The holdout report, last: the survivors are chosen and seeded before any
    # holdout day is read, and nothing above is handed its numbers.
    # Buy and hold of the names in force (plan G9), report only, read here for the
    # same reason: the gate report gets its windows and the holdout report its own.
    holdout_report: dict[str, Any] | None = None
    if admission_layout is not None:
        benchmark_inputs = _benchmark_inputs(
            simulation_runner=simulation_context.simulation_runner,
            layout=admission_layout,
            experiment_def=experiment_def,
            universe_timeline=universe_timeline,
            research_dataset=research_dataset,
        )
        _report_benchmark(
            gate_report=gate_report,
            stage_results=stage_results,
            layout=admission_layout,
            inputs=benchmark_inputs,
        )
        holdout_report = _report_holdout(
            simulation_runner=simulation_context.simulation_runner,
            layout=admission_layout,
            survivors=final_survivors,
            experiment_def=experiment_def,
            universe_timeline=universe_timeline,
            research_dataset=research_dataset,
            benchmark_inputs=benchmark_inputs,
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
            # Of the pipeline's runs, how many were read back from an earlier
            # attempt and not simulated; None when reuse was not asked for.
            "runs_reloaded": (
                len(simulation_context.simulation_runner.reloaded_run_ids)
                if reuse_completed_runs
                else None
            ),
            # One line per family the draw asked for: asked, delivered, rejected,
            # repeats, and whether it ran out of distinct valid settings (plan G2).
            "generation": list(pipeline_result.generation),
            "stage_1_survivors": stage1_count,
            "stage_2_survivors": stage2_count,
            "stage_3_survivors": stage3_count,
            "stage_funnel": stage_funnel,
            "gate_report": gate_report,
            "research_profile": profile,
            "universe_timeline_version": universe_timeline_id,
            "dataset_version": experiment_def.dataset_version,
            "window_start": experiment_def.start_date.isoformat(),
            "window_end": experiment_def.end_date.isoformat(),
            "window_layout": window_layout,
            "admission_window": (
                _admission_window_summary(admission_layout)
                if admission_layout is not None
                else None
            ),
            "holdout_report": holdout_report,
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
    lookback_days: int = DEFAULT_RESEARCH_LOOKBACK_DAYS,
    universe_timeline: UniverseTimeline | None = None,
    research_dataset: ResearchWindowDataset | None = None,
    admission_layout: AdmissionWindowLayout | None = None,
):
    """Build an ExperimentDefinition with the staged pipeline for the monthly research tick.

    Which days each stage reads is decided first, in one of two ways, and the
    stages are then built from that (``_ResearchWindow``):

    The lookback window (no ``admission_layout``). Stage 1 always runs.
    Walk-forward and everything after it (Monte Carlo, regime, stress,
    overfitting) need >= 75 days of data. When they are built, stage 1 selects
    on the days before the first walk-forward test fold only; the later stages
    still run on the whole window.

    The admission layout. Stage 1 selects on the selection piece; walk-forward
    tests on the evaluation piece's three folds, every fold's train window being
    the selection piece; Monte Carlo, regime and stress read the evaluation
    piece. The experiment runs from the selection piece's first day to the last
    day a gate may read.

    The bars come from, in this order: the dataset the job named
    (``research_dataset``), the replay's own cumulative dataset
    (``dataset_version_id_override``), the latest validated raw bars.

    Returns None if no validated dataset is available, or the replay's dataset
    holds under 15 days. Raises ResearchWindowError when the window does not fit
    a dataset the job named.
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
    from autonomous_trading_platform.research.pipeline.stages.neighbour_stage import (
        NeighbourStage,
        NeighbourStageConfig,
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

    if research_dataset is not None:
        # The job named its bars (research.options.dataset_version).
        dataset_version = research_dataset.dataset_version_id
        price_basis = PriceBasis.RAW
    # When running inside a platform backtest, use the pre-created cumulative version
    # directly to avoid picking up stale validated versions from prior runs.
    elif dataset_version_id_override is not None:
        dataset_version = dataset_version_id_override
        price_basis = PriceBasis.RAW
    else:
        # Latest validated raw_bars: research reads raw bars and split-adjusts
        # history on read, like the trading cycle (plan 5d, D5).
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
        price_basis = PriceBasis.RAW

    # Which days each stage reads.
    if admission_layout is not None:
        if research_dataset is None or universe_timeline is None:
            # Not reachable through the options: both are required with the layout.
            raise ValueError("the admission layout needs a named dataset and a universe timeline")
        window = _admission_research_window(admission_layout)
    else:
        lookback_window = _lookback_research_window(
            session=session,
            tick_date=tick_date,
            lookback_days=lookback_days,
            research_dataset=research_dataset,
            dataset_version_id_override=dataset_version_id_override,
        )
        if lookback_window is None:
            return None
        window = lookback_window
    start_date, end_date = window.start_date, window.end_date

    # The names that have bars: the named dataset's, else (in a backtest) the ones
    # the replay ingested. None when neither is known.
    held_symbols: set[str] | None = None
    if research_dataset is not None:
        held_symbols = set(research_dataset.symbols)
    elif dataset_version_id_override is not None:
        held_symbols = set(replay_context.symbols)

    # Survivorship-safe research universe: members active at the START of the
    # research window, not at tick_date. A tick-date universe silently drops
    # names that died inside the window; anchoring at the window start keeps
    # them (their bars simply stop). SurvivorshipGuard enforces the anchor.
    universe_timeline_version: str | None = None
    regime_label_symbols: list[str] | None = None
    if universe_timeline is not None:
        # On a timeline the universe tables are not read (plan, decided
        # 2026-11-18): the timeline is the point-in-time universe. The stages are
        # handed every name of the window; each run loads the names of its own
        # dates, and the engine trades the list in force on each bar.
        scope = _timeline_universe_scope(universe_timeline, start_date, end_date)
        universe_timeline_version = universe_timeline.dataset_version_id
        universe_version = universe_timeline_version
        symbols = universe_timeline.symbols_on_dates(start_date, end_date)
        # Market regimes are labelled from the list in force on the first day the
        # regime stage reads: a name picked later does not shape the labels of
        # the months before it was picked. That day is the window's first day
        # (decided 2026-11-19), and in the admission layout the evaluation
        # piece's first day (decided 2026-11-20).
        label_scope = (
            scope
            if window.gates_start == start_date
            else _timeline_universe_scope(universe_timeline, window.gates_start, end_date)
        )
        regime_label_symbols = sorted(label_scope.member_symbols)
        if held_symbols is not None:
            # Only the names the dataset holds (in a backtest, the ones the replay
            # ingested) have Parquet bars. A timeline's names are not cut down to
            # fit; the run is refused.
            _not_held = sorted(set(symbols) - held_symbols)
            if _not_held:
                from autonomous_trading_platform.universe.services.survivorship_guard import (
                    SurvivorshipBiasError,
                )

                _lacking = (
                    f"dataset {research_dataset.dataset_version_id} does not hold"
                    if research_dataset is not None
                    else "the replay did not ingest"
                )
                raise SurvivorshipBiasError(
                    f"Universe timeline {universe_timeline_version} names symbols "
                    f"{_lacking}: {_not_held}. Running on the rest "
                    "would not be the timeline's universe."
                )
    else:
        scope = _resolve_research_universe_scope(
            session=session, start_date=start_date, end_date=end_date
        )
        universe_version = scope.universe_version_id or "v1"
        symbols = sorted(scope.member_symbols)
        if held_symbols is not None:
            # Only the names the dataset holds (in a backtest, the ones the replay
            # ingested) have Parquet bars.
            symbols = [s for s in symbols if s in held_symbols]
    if not symbols:
        from autonomous_trading_platform.universe.services.survivorship_guard import (
            SurvivorshipBiasError,
        )

        raise SurvivorshipBiasError(
            f"Universe active on {start_date} ({scope.universe_version_id}) has no "
            "ingested symbols — refusing to fall back to an unanchored symbol list."
        )

    experiment_id = f"replay_research_{tick_date.strftime('%Y%m')}"

    default_weights = ScoringWeights()

    # Tighter filter shared by walk-forward and Monte Carlo stages
    wf_mc_filter = FilterConfig(
        min_sharpe=0.3,
        max_drawdown=-0.35,
        min_trades=5,
        min_consistency_score=0.3,
        min_total_return=-0.05,
        max_daily_turnover=RESEARCH_MAX_DAILY_TURNOVER,
    )

    # Stage 1 picks the candidates. When walk-forward follows, stage 1 sees only
    # the days before the first test fold starts, so every test fold is data
    # that had no say in the selection (plan R2). On its own it gets the whole
    # window.
    # Stage 1 — always runs (just needs start_date < end_date)
    stage1 = SimulationStage(
        stage_config=SimulationStageConfig(
            name="initial_quality_filter",
            start_date=start_date,
            end_date=window.selection_end,
            symbols=symbols,
            universe_timeline_version=universe_timeline_version,
            filter_config=FilterConfig(
                min_sharpe=0.5,
                max_drawdown=-0.30,
                min_trades=10,
                min_consistency_score=0.4,
                min_total_return=0.0,
                max_daily_turnover=RESEARCH_MAX_DAILY_TURNOVER,
            ),
            scoring_weights=default_weights,
            max_workers=1,
        ),
        simulation_runner=simulation_runner,
    )

    stages: list = [stage1]

    # Stage 2 — WalkForwardStage: the same fixed config evaluated on train/test
    # sub-periods, laid out as the window says (rolling folds in the lookback
    # window; in the admission layout the three test folds, each with the
    # selection piece as its train window). Its first test fold starts the day
    # after stage 1's selection window ends. Survivors of stage 1 are already
    # filtered, so the fold simulations are cheap.
    if window.fold_layout is not None:
        stage2 = WalkForwardStage(
            stage_config=WalkForwardStageConfig(
                name="walk_forward_robustness",
                symbols=symbols,
                universe_timeline_version=universe_timeline_version,
                start_date=start_date,
                end_date=end_date,
                train_filter_config=wf_mc_filter,
                train_scoring_weights=default_weights,
                test_filter_config=wf_mc_filter,
                test_scoring_weights=default_weights,
                require_all_folds=False,
                max_workers=1,
                **window.fold_layout,
            ),
            simulation_runner=simulation_runner,
        )
        stages.append(stage2)

        # Stage 3 — MonteCarloStage: only survivors of walk-forward enter (typically 2-5).
        # One simulation per survivor; n_runs is the number of resampled paths of
        # its equity curve, which cost milliseconds, so smoke only trims it a little.
        # In the admission layout that one simulation is a continuous run over
        # the evaluation piece, the three test periods as one record, and it
        # must itself clear wf_mc_filter: the pooled half of the pass rule
        # (decided 2026-11-18 and 2026-11-20). The resampled paths do not check
        # that for it: reshuffling scatters a losing streak, so a record with a
        # deep drawdown has mostly shallow paths (measured 2026-11-20).
        stage3 = MonteCarloStage(
            stage_config=MonteCarloStageConfig(
                name="monte_carlo_structural_robustness",
                symbols=symbols,
                universe_timeline_version=universe_timeline_version,
                base_run_clears_filter=window.gate_run_clears_filter,
                start_date=window.gates_start,
                end_date=end_date,
                n_runs=200 if smoke else 2000,
                min_pass_rate=0.6,
                filter_config=wf_mc_filter,
                scoring_weights=default_weights,
                max_workers=1,
            ),
            simulation_runner=simulation_runner,
        )
        stages.append(stage3)

        # Stage 4 — regime robustness on auto-classified daily regimes. Reuses
        # the Monte Carlo base run (same window), so it adds no
        # simulations: one bar read + classification per research tick.
        # Report only (enforce=False): on the ~60-session lookback window a
        # regime holds 18-19 sessions, too few for the thresholds below to tell
        # a strategy from chance. The findings are recorded; nobody is
        # eliminated. Not re-judged for the admission layout's longer window:
        # report only there too.
        stages.append(
            RegimeStage(
                stage_config=RegimeStageConfig(
                    name="regime_robustness",
                    symbols=symbols,
                    universe_timeline_version=universe_timeline_version,
                    label_symbols=regime_label_symbols,
                    start_date=window.gates_start,
                    end_date=end_date,
                    gate=RegimeGateConfig(
                        dimensions=("trend", "volatility"),
                        # Research sims run on 5-min bars: a regime needs ~5
                        # trading days of bars before its metrics mean anything.
                        min_bars_per_regime=5 * BARS_PER_DAY,
                        min_regime_sharpe=-1.0,
                        max_regime_drawdown=-0.20,
                        min_positive_regime_fraction=0.5,
                        on_insufficient_coverage="pass",
                        enforce=False,
                    ),
                ),
                simulation_runner=simulation_runner,
                label_provider=OnTheFlyRegimeLabelProvider.from_simulation_runner(
                    simulation_runner
                ),
            )
        )

        # Stage 5 — stress: execution-cost re-runs decide; equity-curve shocks
        # (free) are reported on the verdict only.
        stages.append(
            StressStage(
                stage_config=StressStageConfig(
                    name="stress_robustness",
                    symbols=symbols,
                    universe_timeline_version=universe_timeline_version,
                    start_date=window.gates_start,
                    end_date=end_date,
                    gate=StressGateConfig(
                        min_shock_sharpe=0.0,
                        max_shock_drawdown=-0.40,
                        cost_multipliers=(2.0,) if smoke else (2.0, 3.0),
                        min_cost_sharpe=0.0,
                        max_cost_drawdown=-0.40,
                        min_cost_survival_rate=0.5,
                    ),
                ),
                simulation_runner=simulation_runner,
            )
        )

        # Parameter neighbours (admission layout only): each survivor re-run with
        # one setting moved a little, down and up, over the same days as the gates
        # above. Report only: the thresholds are the gate's defaults and have not
        # been measured against anything; every neighbour's numbers are recorded.
        # Up to two simulations per setting per survivor, so it sits after the
        # stages that eliminate. Its verdict is not handed to the overfitting
        # stage below as parameter fragility: that would move a score other code
        # reads.
        if window.neighbour_check:
            stages.append(
                NeighbourStage(
                    stage_config=NeighbourStageConfig(
                        name="parameter_neighbours",
                        symbols=symbols,
                        universe_timeline_version=universe_timeline_version,
                        start_date=window.gates_start,
                        end_date=end_date,
                    ),
                    simulation_runner=simulation_runner,
                )
            )

        # Last stage — overfitting: no simulations; combines WF/MC/regime evidence.
        # Report only (enforce=False): on the lookback window the walk-forward
        # evidence is two folds at most, and on real bars the gate changed no
        # outcome. The score and findings are recorded; nobody is eliminated.
        # Not re-judged for the admission layout's three folds: report only
        # there too.
        stages.append(
            OverfittingStage(
                stage_config=OverfittingStageConfig(
                    name="overfitting_gate",
                    gate=OverfittingGateConfig(
                        max_overfitting_probability=0.6,
                        min_core_indicators=2,
                        min_trade_count=10,
                        on_insufficient_evidence="pass",
                        enforce=False,
                    ),
                )
            )
        )

    month_seed = int(tick_date.strftime("%Y%m"))
    return ExperimentDefinition(
        experiment_id=experiment_id,
        experiment_type=ExperimentType.SWEEP,
        description=f"Monthly platform replay research — {tick_date.strftime('%B %Y')}",
        strategy_set=_replay_strategy_set(smoke=smoke, seed=month_seed),
        parameter_grid=[{}],
        dataset_version=dataset_version,
        universe_version=universe_version,
        price_basis=price_basis,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        random_seed=month_seed,
        parameter_space={},
        # In the admission layout the validator is told the holdout date and
        # refuses a stage that reads it. The lookback layout sets no holdout aside.
        staged_pipeline_config=StagedPipelineConfig(
            stages=stages,
            holdout_start=admission_layout.holdout_start if admission_layout is not None else None,
        ),
    )


def _lookback_research_window(
    *,
    session: Session,
    tick_date: date,
    lookback_days: int,
    research_dataset: ResearchWindowDataset | None,
    dataset_version_id_override: str | None,
) -> _ResearchWindow | None:
    """The window ``lookback_days`` long, ending on the tick's date or on the last day
    of a named dataset that ends before it.

    None when the replay's dataset holds under 15 days. Raises ResearchWindowError when
    the window does not fit a dataset the job named.
    """
    end_date = tick_date
    if research_dataset is not None:
        if tick_date < research_dataset.first_day:
            raise ResearchWindowError(
                f"the research date {tick_date} is before the first day of dataset "
                f"{research_dataset.dataset_version_id} ({research_dataset.first_day})"
            )
        end_date = min(tick_date, research_dataset.last_day)
    lookback_start = end_date - timedelta(days=lookback_days)
    # Clamp to the dataset's coverage start so walk-forward folds don't request
    # data before the replay began (cumulative dataset has no pre-replay bars).
    # A named dataset is not clamped to: a window it cannot hold is refused.
    replay_start: date | None = None
    if research_dataset is not None:
        if lookback_start < research_dataset.first_day:
            raise ResearchWindowError(
                f"a window of {lookback_days} days ending {end_date} starts on "
                f"{lookback_start}, before the first day of dataset "
                f"{research_dataset.dataset_version_id} ({research_dataset.first_day})"
            )
        if lookback_days < MIN_RESEARCH_WINDOW_DAYS:
            raise ResearchWindowError(
                f"a window of {lookback_days} days is under the "
                f"{MIN_RESEARCH_WINDOW_DAYS} a research run needs"
            )
    elif dataset_version_id_override is not None:
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

    available_days = (end_date - start_date).days

    # Need at least 15 calendar days for stage 1 to be meaningful.
    # Guards against monthly cadence firing on day 1 (last=None → True).
    if available_days < MIN_RESEARCH_WINDOW_DAYS:
        return None

    # Walk-forward needs train_days + test_days of data (75 days); the stages
    # after it (Monte Carlo, regime, stress, overfitting) are built only with it.
    has_walk_forward = available_days >= _ROLLING_TRAIN_DAYS + _ROLLING_TEST_DAYS
    return _ResearchWindow(
        start_date=start_date,
        end_date=end_date,
        # With walk-forward, stage 1 stops the day before the first test fold starts.
        selection_end=(
            start_date + timedelta(days=_ROLLING_TRAIN_DAYS - 1) if has_walk_forward else end_date
        ),
        # The gates after walk-forward read the whole window.
        gates_start=start_date,
        fold_layout=_ROLLING_FOLD_LAYOUT if has_walk_forward else None,
    )


def _lay_out_admission_window(
    *, tick_date: date, research_dataset: ResearchWindowDataset
) -> AdmissionWindowLayout:
    """The admission layout on a named dataset, as of the tick's date.

    Sessions are the NYSE calendar's, less the sessions the dataset left out by name:
    a day with no bars is no session of a piece or a fold.

    Raises ResearchWindowError when the dataset cannot hold the layout, or the research
    runs before the holdout date.
    """
    from autonomous_trading_platform.research.pipeline.admission_window import (
        AdmissionWindowError,
        admission_window_layout,
    )
    from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START

    try:
        return admission_window_layout(
            holdout_start=HOLDOUT_START,
            as_of=tick_date,
            data_first=research_dataset.first_day,
            data_last=research_dataset.last_day,
            trading_days=_research_sessions(research_dataset),
        )
    except AdmissionWindowError as exc:
        raise ResearchWindowError(
            f"the admission layout cannot be laid on dataset "
            f"{research_dataset.dataset_version_id}: {exc}"
        ) from exc


def _admission_research_window(layout: AdmissionWindowLayout) -> _ResearchWindow:
    """Which days each stage reads in the admission layout.

    Every fold's train window is the selection piece (decided 2026-11-20): with train
    windows that roll, fold 2 would train on fold 1's test days.
    """
    from autonomous_trading_platform.research.pipeline.admission_window import (
        MIN_TEST_FOLDS_PASSED,
    )
    from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
        FoldWindow,
    )

    selection = layout.selection
    folds = tuple(
        FoldWindow(
            train_start=selection.first,
            train_end=selection.last,
            test_start=fold.first,
            test_end=fold.last,
        )
        for fold in layout.test_folds
    )
    return _ResearchWindow(
        start_date=layout.start,
        end_date=layout.last_gate_day,
        selection_end=selection.last,
        gates_start=layout.evaluation.first,
        fold_layout={"folds": folds, "min_folds_passed": MIN_TEST_FOLDS_PASSED},
        # The other half of the pass rule: the evaluation piece as one record.
        gate_run_clears_filter=True,
        # Decided 2026-11-23: every setting, one at a time, report only. Not in the
        # lookback layout, whose jobs run research every month of a long backtest:
        # whether they should pay for it has not been asked.
        neighbour_check=True,
    )


def _report_gates(stage_results: Sequence[StageResult]) -> dict[str, Any]:
    """What every stage said about every strategy
    (``research/pipeline/gate_report.py``). Report only: the survivors are already
    chosen, and a report that cannot be built is said so in its place and does not fail
    the research step.
    """
    from autonomous_trading_platform.research.pipeline.gate_report import (
        build_gate_report,
        gate_report_not_built,
    )

    try:
        return build_gate_report(stage_results)
    except Exception as exc:
        logger.warning("gate_report: not built", exc_info=True)
        return gate_report_not_built(f"failed: {exc}")


def _report_holdout(
    *,
    simulation_runner,
    layout: AdmissionWindowLayout,
    survivors,  # list[StrategyConfig]
    experiment_def,  # ExperimentDefinition
    universe_timeline: UniverseTimeline | None,
    research_dataset: ResearchWindowDataset | None,
    benchmark_inputs: _BenchmarkInputs | str | None = None,
) -> dict[str, Any]:
    """Each final survivor run once over the holdout sessions there are so far
    (``research/pipeline/holdout_report.py``). Report only: whatever goes wrong here is
    written into the report and the research step's outcome is not changed.

    With ``benchmark_inputs`` (plan G9) the report also holds buy and hold over the
    holdout and each candidate's ``versus_benchmark``.
    """
    from autonomous_trading_platform.research.pipeline.holdout_report import (
        HoldoutRunSettings,
        holdout_not_scored,
        score_holdout,
    )

    span = layout.holdout
    if span is None:
        return holdout_not_scored(None, "the dataset holds no holdout session yet")
    if not survivors:
        return holdout_not_scored(span, "no strategy passed every stage")
    if universe_timeline is None or research_dataset is None:
        # Not reachable through the options: the layout is refused without either.
        return holdout_not_scored(span, "the layout was run without a timeline or a dataset")
    try:
        # The names of the lists in force during the holdout; as for the stages, a
        # timeline's names are not cut down to the ones the dataset holds.
        symbols = universe_timeline.symbols_on_dates(span.first, span.last)
        if not symbols:
            return holdout_not_scored(
                span,
                f"universe timeline {universe_timeline.dataset_version_id} has no list in "
                "force during the holdout",
            )
        not_held = sorted(set(symbols) - set(research_dataset.symbols))
        if not_held:
            return holdout_not_scored(
                span,
                f"dataset {research_dataset.dataset_version_id} does not hold the universe's "
                f"names for the holdout: {not_held}",
            )
        benchmark_block, compare = _holdout_benchmark(span, benchmark_inputs)
        with simulation_runner.reuse_loaded_bars():
            report = score_holdout(
                simulation_runner=simulation_runner,
                survivors=survivors,
                span=span,
                compare=compare,
                settings=HoldoutRunSettings(
                    experiment_id=experiment_def.experiment_id,
                    dataset_version=experiment_def.dataset_version,
                    random_seed=experiment_def.random_seed,
                    price_basis=experiment_def.price_basis,
                    initial_cash=experiment_def.initial_cash,
                    resample_to_daily=experiment_def.resample_to_daily,
                    symbols=symbols,
                    universe_timeline_version=universe_timeline.dataset_version_id,
                ),
            )
        report["benchmark"] = benchmark_block
        return report
    except Exception as exc:
        logger.warning("holdout_report: not scored", exc_info=True)
        return holdout_not_scored(span, f"failed: {exc}")


def _research_sessions(
    research_dataset: ResearchWindowDataset,
) -> Callable[[date, date], list[date]]:
    """A named dataset's sessions: the NYSE calendar's, less the ones it left out by
    name (a day with no bars is no session). The admission layout and buy and hold
    both count days by this one rule."""
    from autonomous_trading_platform.runtime.clock import RealMarketCalendar

    calendar = RealMarketCalendar()
    left_out = frozenset(research_dataset.excluded_sessions)

    def sessions(first: date, last: date) -> list[date]:
        return [day for day in calendar.trading_days(first, last) if day not in left_out]

    return sessions


@dataclass(frozen=True)
class _BenchmarkInputs:
    """What buy and hold is computed from, read once for every window (plan G9)."""

    closes_by_symbol: dict[str, list[tuple[datetime, float]]]
    actions: list[CorporateAction]
    members_at: Callable[[datetime], frozenset[str]]
    # The layout's sessions, from the selection's first to the last one read.
    sessions: frozenset[date]
    # Names in force that the dataset holds no bars of: left out, and said so.
    names_without_bars: list[str]


def _benchmark_inputs(
    *,
    simulation_runner,
    layout: AdmissionWindowLayout,
    experiment_def,  # ExperimentDefinition
    universe_timeline: UniverseTimeline | None,
    research_dataset: ResearchWindowDataset | None,
) -> _BenchmarkInputs | str:
    """Each name in force's first and last close of every day from the selection's
    first session to the last one there is, and their splits and cash dividends; or,
    when they cannot be read, why (written into the reports in their place)."""
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from autonomous_trading_platform.research.pipeline.benchmark_report import (
        read_daily_closes,
    )

    if universe_timeline is None or research_dataset is None:
        # Not reachable through the options: the layout is refused without either.
        return "the layout was run without a timeline or a dataset"
    if experiment_def.price_basis != PriceBasis.RAW:
        return f"buy and hold is read from raw bars; the run used {experiment_def.price_basis}"
    last = layout.holdout.last if layout.holdout is not None else layout.last_gate_day
    try:
        in_force = universe_timeline.symbols_on_dates(layout.start, last)
        held = set(research_dataset.symbols)
        symbols = [symbol for symbol in in_force if symbol in held]
        resolved = simulation_runner.dataset_resolver.resolve_bars_dataset(
            dataset_version=experiment_def.dataset_version,
            price_basis=experiment_def.price_basis,
        )
        closes = read_daily_closes(
            reader=simulation_runner.window_loader.bar_reader,
            dataset=resolved.dataset,
            dataset_version=experiment_def.dataset_version,
            symbols=symbols,
            start_date=layout.start,
            end_date=last,
        )
        source = simulation_runner.corporate_action_source
        actions = (
            source.actions_for(symbols=symbols, start_date=layout.start, end_date=last)
            if source is not None
            else []
        )
    except Exception as exc:
        logger.warning("benchmark: inputs not read", exc_info=True)
        return f"failed: {exc}"
    return _BenchmarkInputs(
        closes_by_symbol=closes,
        actions=list(actions),
        members_at=universe_timeline.members_at,
        sessions=frozenset(_research_sessions(research_dataset)(layout.start, last)),
        names_without_bars=sorted(set(in_force) - held),
    )


def _report_benchmark(
    *,
    gate_report: dict[str, Any],
    stage_results: Sequence[StageResult],
    layout: AdmissionWindowLayout,
    inputs: _BenchmarkInputs | str,
) -> None:
    """Buy and hold over the windows the gates read, and every strategy against it,
    written into the gate report (``research/pipeline/benchmark_report.py``). Report
    only: what goes wrong is written in its place."""
    from autonomous_trading_platform.research.pipeline.benchmark_report import (
        add_to_gate_report,
        admission_benchmark,
        benchmark_not_built,
    )

    if "strategies" not in gate_report:
        return  # the gate report itself was not built and says why
    if isinstance(inputs, str):
        gate_report["benchmark"] = benchmark_not_built(inputs)
        return
    try:
        benchmark = admission_benchmark(
            layout=layout,
            closes_by_symbol=inputs.closes_by_symbol,
            actions=inputs.actions,
            members_at=inputs.members_at,
            sessions=inputs.sessions,
        )
        add_to_gate_report(gate_report, stage_results, benchmark)
        gate_report["benchmark"].update(_benchmark_provenance(inputs))
    except Exception as exc:
        logger.warning("benchmark: not built", exc_info=True)
        gate_report["benchmark"] = benchmark_not_built(f"failed: {exc}")


def _benchmark_provenance(inputs: _BenchmarkInputs) -> dict[str, Any]:
    """What buy and hold was built from, beside its numbers. The corporate actions are
    the run's own (the database the job runs on): with none, a split reads as a fall,
    for buy and hold and for every candidate alike."""
    return {
        "names_without_bars": inputs.names_without_bars,
        "corporate_actions": {
            "splits": sum(1 for action in inputs.actions if _is_split_action(action)),
            "cash_dividends": sum(1 for action in inputs.actions if not _is_split_action(action)),
        },
    }


def _is_split_action(action: CorporateAction) -> bool:
    from autonomous_trading_platform.accounting.corporate_actions import is_split

    return is_split(action)


def _holdout_benchmark(
    span: SessionSpan, inputs: _BenchmarkInputs | str | None
) -> tuple[dict[str, Any], Callable[[SimulationRunResult], dict[str, Any]] | None]:
    """Buy and hold over the holdout, and the comparison each holdout run is given."""
    from autonomous_trading_platform.research.pipeline.benchmark_report import (
        benchmark_not_built,
        holdout_benchmark,
        run_versus_benchmark,
    )

    if inputs is None:
        return benchmark_not_built("not asked for"), None
    if isinstance(inputs, str):
        return benchmark_not_built(inputs), None
    try:
        series = holdout_benchmark(
            span=span,
            closes_by_symbol=inputs.closes_by_symbol,
            actions=inputs.actions,
            members_at=inputs.members_at,
            sessions=inputs.sessions,
        )
        block = {**series.block(), **_benchmark_provenance(inputs)}
    except Exception as exc:
        logger.warning("benchmark: holdout not built", exc_info=True)
        return benchmark_not_built(f"failed: {exc}"), None
    return block, lambda result: run_versus_benchmark(result, series)


def _admission_window_summary(layout: AdmissionWindowLayout) -> dict[str, Any]:
    """The layout as a research step reports it: each piece's first and last session
    and how many it holds. The holdout is listed, not read."""

    def span(piece: SessionSpan) -> dict[str, Any]:
        return {
            "first": piece.first.isoformat(),
            "last": piece.last.isoformat(),
            "sessions": piece.sessions,
        }

    return {
        "selection": span(layout.selection),
        "evaluation": span(layout.evaluation),
        "test_folds": [span(fold) for fold in layout.test_folds],
        "holdout_start": layout.holdout_start.isoformat(),
        "holdout": span(layout.holdout) if layout.holdout is not None else None,
    }


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


def _timeline_universe_scope(timeline: UniverseTimeline, start_date: date, end_date: date):
    """Point-in-time universe scope for a research window on a universe timeline.

    Raises SurvivorshipBiasError when the window starts before the timeline.
    """
    from autonomous_trading_platform.universe.services.timeline_universe_scope import (
        universe_scope_from_timeline,
    )

    return universe_scope_from_timeline(timeline, start_date, end_date)


def _universe_timeline_source(session: Session) -> Callable[[str], UniverseTimeline]:
    """Reads an exported universe timeline by its version id, once per id. The read
    recomputes the timeline's content hash and refuses one that differs from the
    registered hash (``load_universe_timeline``)."""
    from autonomous_trading_platform.application.services.universe_timeline_loader import (
        load_universe_timeline,
    )

    read: dict[str, UniverseTimeline] = {}

    def source(version_id: str) -> UniverseTimeline:
        if version_id not in read:
            read[version_id] = load_universe_timeline(session, version_id)
        return read[version_id]

    return source


def _resolve_universe_timeline_option(research_options: dict | None) -> str | None:
    raw: object = (research_options or {}).get("universe_timeline")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "research.options.universe_timeline must be the dataset version id of an "
            f"exported universe timeline, got {raw!r}"
        )
    return raw.strip()


def _resolve_dataset_version_option(research_options: dict | None) -> str | None:
    raw: object = (research_options or {}).get("dataset_version")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "research.options.dataset_version must be the dataset version id of a "
            f"research-window dataset, got {raw!r}"
        )
    return raw.strip()


def _resolve_window_layout_option(research_options: dict | None) -> str | None:
    """The layout a job asked for, or None for the lookback window.

    A value that is not a known layout is an error, never the lookback window: the run
    would look valid and be a different experiment. So is the admission layout without
    the two things it is defined on, or together with a window length.
    """
    options = research_options or {}
    raw: object = options.get("window_layout")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in _WINDOW_LAYOUTS:
        raise ValueError(
            f"research.options.window_layout must be one of {sorted(_WINDOW_LAYOUTS)}, or "
            f"left out for the lookback_days window, got {raw!r}"
        )
    if options.get("universe_timeline") is None:
        raise ValueError(
            f"research.options.window_layout {raw!r} needs research.options.universe_timeline: "
            "the layout runs on a universe timeline, not on one list for the whole window"
        )
    if options.get("dataset_version") is None:
        raise ValueError(
            f"research.options.window_layout {raw!r} needs research.options.dataset_version: "
            "the layout runs on a research-window dataset the job names"
        )
    if "lookback_days" in options:
        raise ValueError(
            f"research.options.lookback_days does not apply with window_layout {raw!r}: "
            "the layout's length follows from the holdout date; leave lookback_days out"
        )
    return raw


_RESEARCH_PROFILES = frozenset({"full", "smoke"})


def _resolve_lookback_days(research_options: dict | None) -> int:
    raw: object = (research_options or {}).get("lookback_days", DEFAULT_RESEARCH_LOOKBACK_DAYS)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"research.options.lookback_days must be a positive integer, got {raw!r}")
    return raw


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


def _replay_strategy_set(*, smoke: bool, seed: int) -> list[dict]:
    """Candidate mix per research tick. Smoke keeps every family at 2 samples each.

    ``seed`` is the research month (``YYYYMM``), the seed the tick's simulations use, so
    each month draws its own candidates (plan G1; before, every month drew the
    generator's default seed, 42, and so the same set). Every family gets the same seed:
    each draw starts its own generator over a different space.
    """
    full_counts = {
        "momentum": 6,
        "mean_reversion": 6,
        "moving_average_crossover": 6,
        "factor_based": 8,
        "composite_rule": 10,
    }
    return [
        {"type": t, "method": "random", "options": {"n_samples": 2 if smoke else n, "seed": seed}}
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
        existing = _latest_governance_row(session, strategy_id)
        if existing is None:
            session.add(
                StrategyGovernance(
                    strategy_id=strategy_id,
                    config_hash=config_hash,
                    current_state="candidate",
                    experiment_id=experiment_id,
                    source_run_id=source_run_id,
                    submitted_at=now_utc,
                    updated_at=now_utc,
                    submitted_by="system",
                )
            )
        elif (
            existing.current_state == "candidate"
            and existing.source_run_id is None
            and source_run_id is not None
        ):
            # Back-fill source_run_id on rows seeded by a previous run that lacked it.
            existing.source_run_id = source_run_id
            existing.updated_at = now_utc
    try:
        session.flush()
    except Exception:
        logger.exception("_seed_strategy_promotions: flush failed — rolling back")
        session.rollback()
        raise


def _latest_governance_row(session: Session, strategy_id: str) -> StrategyGovernance | None:
    """Latest governance row for a strategy, whatever config_hash it was seeded with.

    Strategy ids are content hashes, so an existing row means research already
    produced this exact config. Keying on strategy_id alone (not (id, config_hash),
    which different seeders hash differently) keeps research from re-seeding a
    strategy as a fresh candidate — in particular one the bench review retired.
    """
    row: StrategyGovernance | None = session.scalars(
        select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == strategy_id)
        .order_by(StrategyGovernance.updated_at.desc())
        .limit(1)
    ).first()
    return row


def _seed_research_governance(
    *,
    session: Session,
    survivors,  # list[FilterScoreOutput]
    experiment_id: str,
    now_utc: datetime,
) -> None:
    """Upsert StrategyGovernance rows for research survivors in candidate state."""
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    for output in survivors:
        strategy_id = output.strategy_id
        config_hash = hashlib.sha256(strategy_id.encode()).hexdigest()[:16]
        if _latest_governance_row(session, strategy_id) is None:
            session.add(
                StrategyGovernance(
                    strategy_id=strategy_id,
                    config_hash=config_hash,
                    current_state="candidate",
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
