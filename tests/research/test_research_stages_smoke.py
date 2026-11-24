"""Small-window smoke tests for the regime / stress / overfitting research stages.

Same pattern as test_research_hooks_smoke.py: synthetic daily bars in a temp
Parquet root, SQLite session, real SimulationRunner. ~5 months of bars whose
trend flips halfway through, so the on-the-fly regime classifier sees both
bull and bear days.

Run with:
    python -m pytest tests/research/test_research_stages_smoke.py -v
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.metadata import attach_metadata, build_metadata
from autonomous_trading_platform.storage.parquet.paths import partition_file_path
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from tests.utilities.universe_seeding import seed_universe_version

_SYMBOLS = ["AAPL", "MSFT", "SPY"]
_DATASET_VERSION_ID = "research_stages_smoke_v1"
_START_DATE = date(2023, 1, 3)
_END_DATE = date(2023, 5, 31)
_TICK_TS = datetime(2023, 5, 31, 21, 0, tzinfo=UTC)

_PERMISSIVE_FILTER = {
    "min_sharpe": -1e9,
    "max_drawdown": -1.0,
    "min_trades": 0,
    "min_consistency_score": 0.0,
    "min_profit_factor": 0.0,
    "min_total_return": -1.0,
}
_MINIMAL_STRATEGY_SET = [
    {"type": t, "method": "random", "options": {"n_samples": 1}}
    for t in ("momentum", "mean_reversion", "moving_average_crossover")
]


def _trading_days(start: date, end: date) -> list[date]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _make_bars(symbol: str, trading_days: list[date]) -> list[dict]:
    """Up-trend for the first half, down-trend for the second, with noise."""
    rng = np.random.default_rng(sum(map(ord, symbol)))
    half = len(trading_days) // 2
    drift = np.where(np.arange(len(trading_days)) < half, 0.004, -0.004)
    closes = 100.0 * np.cumprod(1 + drift + rng.normal(0, 0.01, len(trading_days)))
    now = datetime(2023, 1, 1, tzinfo=UTC)
    bars = []
    for i, (d, close) in enumerate(zip(trading_days, closes, strict=True)):
        ts = datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)
        bars.append(
            {
                "bar_id": f"{symbol}_{d.isoformat()}_1d",
                "timestamp": ts,
                "end_timestamp": ts + timedelta(hours=6, minutes=30),
                "interval": BarInterval.ONE_DAY.value,
                "symbol": symbol,
                "open": float(close) * 0.998,
                "high": float(close) * 1.01,
                "low": float(close) * 0.99,
                "close": float(close),
                "volume": 1_000_000 + i * 5_000,
                "vwap": float(close),
                "trade_count": 50_000,
                "price_basis": PriceBasis.RAW.value,
                "adjustment_factor": 1.0,
                "source": "stages_smoke_test",
                "ingested_at": now,
                "quality_flags": None,
                "date": d,
                "year": f"{d.year:04d}",
                "month": f"{d.month:02d}",
            }
        )
    return bars


def _seed_bar_parquet(data_root: Path, symbol: str, bars: list[dict]) -> None:
    by_month: dict[tuple[str, str], list[dict]] = {}
    for bar in bars:
        by_month.setdefault((bar["year"], bar["month"]), []).append(bar)
    for (year, month), month_bars in by_month.items():
        file_path = partition_file_path(
            base_path=data_root,
            dataset=RAW_BARS_DATASET,
            dataset_version=_DATASET_VERSION_ID,
            partitions={"symbol": symbol, "year": year, "month": month},
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {f.name: [b.get(f.name) for b in month_bars] for f in BAR_SCHEMA}, schema=BAR_SCHEMA
        )
        meta = build_metadata(
            dataset=RAW_BARS_DATASET,
            dataset_version=_DATASET_VERSION_ID,
            ingestion_timestamp=_TICK_TS.isoformat(),
            checksum="stages_smoke_checksum",
        )
        pq.write_table(table.cast(attach_metadata(table.schema, meta)), file_path)


@pytest.fixture()
def smoke_data_root(tmp_path: Path, db_session, monkeypatch) -> Path:
    for symbol in _SYMBOLS:
        _seed_bar_parquet(
            tmp_path, symbol, _make_bars(symbol, _trading_days(_START_DATE, _END_DATE))
        )

    db_session.add(
        DatasetVersions(
            dataset_version_id=_DATASET_VERSION_ID,
            dataset_name="raw_bars",
            created_at=_TICK_TS,
            source="stages_smoke_test",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version=RAW_BARS_DATASET.schema_version,
            symbol_coverage=len(_SYMBOLS),
            date_coverage_start=_START_DATE,
            date_coverage_end=_END_DATE,
            validation_status="validated",
            checksum="stages_smoke_checksum",
        )
    )
    db_session.flush()

    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(parquet_paths, "get_data_root", lambda: tmp_path, raising=False)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PARQUET_DATA_ROOT", str(tmp_path))

    import autonomous_trading_platform.research.simulation.contexts.build_simulation_context as ctx_mod

    orig_build = ctx_mod.build_simulation_context

    def _patched_build(*, session, universe_size=None, **kwargs):
        ctx = orig_build(session=session, universe_size=universe_size, **kwargs)
        ctx.bar_reader.base_path = tmp_path
        ctx.dataset_resolver.base_path = tmp_path
        ctx.window_loader.bar_reader.base_path = tmp_path
        if getattr(ctx.window_loader, "feature_reader", None):
            ctx.window_loader.feature_reader.base_path = tmp_path
        ctx.simulation_runner.dataset_resolver.base_path = tmp_path
        return ctx

    monkeypatch.setattr(ctx_mod, "build_simulation_context", _patched_build)
    return tmp_path


@pytest.fixture()
def pit_universe(smoke_data_root, db_session) -> str:
    """Universe active from the dataset start (what backtest bootstrap creates)."""
    return seed_universe_version(
        db_session,
        symbols=_SYMBOLS,
        effective_from=datetime.combine(_START_DATE, datetime.min.time(), tzinfo=UTC),
    )


@pytest.fixture()
def replay_context() -> PlatformReplayContext:
    return PlatformReplayContext.create(symbols=_SYMBOLS, actor="stages_smoke", timestamp=_TICK_TS)


def _build_context(db_session):
    import autonomous_trading_platform.research.simulation.contexts.build_simulation_context as ctx_mod

    return ctx_mod.build_simulation_context(session=db_session, universe_size=len(_SYMBOLS))


def _replay_experiment(db_session, replay_context, simulation_runner, profile: str = "smoke"):
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    exp = rh._build_replay_experiment_definition(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        simulation_runner=simulation_runner,
        dataset_version_id_override=_DATASET_VERSION_ID,
        profile=profile,
    )
    assert exp is not None, "replay experiment should build from the seeded dataset"
    return exp


def test_replay_definition_has_all_six_stages_with_smoke_budget(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
        MonteCarloStage,
    )
    from autonomous_trading_platform.research.pipeline.stages.stress_stage import StressStage

    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner)

    stages = exp.staged_pipeline_config.stages
    assert [type(s).__name__ for s in stages] == [
        "SimulationStage",
        "WalkForwardStage",
        "MonteCarloStage",
        "RegimeStage",
        "StressStage",
        "OverfittingStage",
    ]
    mc = next(s for s in stages if isinstance(s, MonteCarloStage))
    stress = next(s for s in stages if isinstance(s, StressStage))
    assert mc._cfg.n_runs == 200
    assert stress._cfg.gate.cost_multipliers == (2.0,)
    assert all(item["options"]["n_samples"] == 2 for item in exp.strategy_set)

    full = _replay_experiment(db_session, replay_context, ctx.simulation_runner, profile="full")
    full_mc = next(s for s in full.staged_pipeline_config.stages if isinstance(s, MonteCarloStage))
    assert full_mc._cfg.n_runs == 2000


def test_stage_one_selects_before_the_first_walk_forward_test_fold(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """Test folds are out of sample only if stage 1 never saw their dates (plan R2)."""
    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner)
    sim, wf, *later = exp.staged_pipeline_config.stages

    first_test_start = wf._cfg.start_date + timedelta(days=wf._cfg.train_days)
    assert sim._stage_config.start_date == wf._cfg.start_date == exp.start_date
    assert sim._stage_config.end_date == first_test_start - timedelta(days=1)
    # Walk-forward and the robustness stages after it keep the whole window.
    assert wf._cfg.end_date == exp.end_date
    assert all(s._cfg.end_date == exp.end_date for s in later if hasattr(s._cfg, "end_date"))


def test_report_only_gates_do_not_feed_the_deciding_scores(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """A gate that is not trusted to eliminate must not carry weight in the scores
    that decide (plan R5 / R16). The gates' enforce flags and the evidence policy
    are set in different files; this fails when one changes without the other."""
    from autonomous_trading_platform.research.pipeline.stages.overfitting_stage import (
        OverfittingStage,
    )
    from autonomous_trading_platform.research.pipeline.stages.regime_stage import RegimeStage
    from autonomous_trading_platform.research.validation.evidence_policy import (
        DEFAULT_EVIDENCE_POLICY,
        OVERFITTING,
        REGIME,
        STRESS,
    )

    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner, profile="full")
    stages = exp.staged_pipeline_config.stages
    regime = next(s for s in stages if isinstance(s, RegimeStage))
    overfitting = next(s for s in stages if isinstance(s, OverfittingStage))

    assert DEFAULT_EVIDENCE_POLICY.decides(REGIME) == regime._cfg.gate.enforce
    assert DEFAULT_EVIDENCE_POLICY.decides(OVERFITTING) == overfitting._cfg.gate.enforce
    # The stress component is the shock survival rate, which no gate enforces
    # (the stress stage eliminates on its cost re-simulations only).
    assert not DEFAULT_EVIDENCE_POLICY.decides(STRESS)


def test_stage_one_alone_keeps_the_whole_window(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """Under 75 days of data there is no walk-forward, so nothing to hold back for."""
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    early_tick = datetime(2023, 3, 1, 21, 0, tzinfo=UTC)  # 57 days after the data starts
    ctx = _build_context(db_session)
    exp = rh._build_replay_experiment_definition(
        session=db_session,
        timestamp=early_tick,
        replay_context=replay_context,
        simulation_runner=ctx.simulation_runner,
        dataset_version_id_override=_DATASET_VERSION_ID,
        profile="smoke",
    )

    assert exp is not None
    (only_stage,) = exp.staged_pipeline_config.stages
    assert only_stage._stage_config.end_date == early_tick.date() == exp.end_date


def _permissive_stage_dicts(exp, *, selection_end: date | None = None) -> list[dict]:
    """The six stages with gates that pass everything; stage 1 ends at selection_end."""
    window = {
        "symbols": list(exp.symbols),
        "start_date": exp.start_date.isoformat(),
        "end_date": exp.end_date.isoformat(),
    }
    sim_window = {**window, "end_date": (selection_end or exp.end_date).isoformat()}
    return [
        {"type": "simulation", "name": "sim", "filter_config": _PERMISSIVE_FILTER, **sim_window},
        {
            "type": "walk_forward",
            "name": "wf",
            "train_days": 45,
            "test_days": 30,
            "allow_short_test_folds": True,
            "step_days": 15,
            "allow_overlapping_test_folds": True,  # the hook's layout
            "require_all_folds": False,
            "train_filter_config": _PERMISSIVE_FILTER,
            "test_filter_config": _PERMISSIVE_FILTER,
            **window,
        },
        {
            "type": "monte_carlo",
            "name": "mc",
            "n_runs": 200,
            "min_pass_rate": 0.5,
            "filter_config": _PERMISSIVE_FILTER,
            **window,
        },
        {
            "type": "regime",
            "name": "regime",
            "min_bars_per_regime": 5,
            "min_regime_sharpe": -1e9,
            "max_regime_drawdown": -1.0,
            "min_positive_regime_fraction": 0.0,
            **window,
        },
        {
            "type": "stress",
            "name": "stress",
            "cost_multipliers": [3.0],
            "min_cost_survival_rate": 0.0,
            **window,
        },
        {"type": "overfitting", "name": "overfit", "max_overfitting_probability": 1.0},
    ]


def test_every_new_stage_runs_on_real_simulations(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """Permissive upstream filters so survivors reach regime → stress → overfitting."""
    from autonomous_trading_platform.research.pipeline.pipeline_runner import (
        StagedPipelineConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry

    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner)
    stages = [
        StageRegistry.load(raw, ctx.simulation_runner) for raw in _permissive_stage_dicts(exp)
    ]
    exp = replace(
        exp,
        strategy_set=_MINIMAL_STRATEGY_SET,
        # smoke: the filters pass everything and stage 1 reads the whole window.
        staged_pipeline_config=StagedPipelineConfig(stages=stages, smoke=True),
    )

    result = ctx.experiment_orchestration_service.run_staged_experiment(exp)
    by_name = {sr.stage_name: sr for sr in result.stage_results}

    assert list(by_name) == ["sim", "wf", "mc", "regime", "stress", "overfit"]
    # One pipeline run shares the bars its simulations load and lets go of them at
    # the end (SimulationWindowLoader.reuse_bars).
    loader = ctx.simulation_runner.window_loader
    assert loader.bar_reuses > 0
    assert loader._loaded is None
    survivors = [c.strategy_id for c in by_name["mc"].survivors]
    assert survivors, "permissive filters should let strategies reach the robustness stages"
    assert [c.strategy_id for c in result.final_survivors] == survivors

    # Monte Carlo: one real simulation per strategy, resampled into a real distribution.
    # In this pipeline stage 1 read these same days (smoke), so its run is the one
    # resampled and Monte Carlo simulates none of its own.
    mc = by_name["mc"]
    assert mc.simulation_results == []
    assert len(mc.filter_outputs) == len(survivors)
    stage_1_run = {run.strategy_id: run for run in by_name["sim"].simulation_results}
    for sid in survivors:
        assert mc.diagnostics[sid].reference_result is stage_1_run[sid]
    for sid in survivors:
        aggregation = mc.diagnostics[sid].mc_aggregation
        assert aggregation is not None
        assert aggregation.n_runs == 200
        assert aggregation.n_sessions > 30  # daily bars: one session per bar return

    # Regime: labels classified from real bars; MC's base run reused.
    regime = by_name["regime"]
    assert regime.simulation_results == []
    for sid in survivors:
        profile = regime.diagnostics[sid].regime_profile
        assert profile is not None
        labelled = {
            label for label, m in profile.by_trend.metrics_by_label.items() if m.bar_count > 0
        }
        assert labelled, "trend regimes should be labelled on the smoke window"

    # Stress: exactly one real 3x-cost re-simulation per survivor; costs never help.
    stress = by_name["stress"]
    assert len(stress.simulation_results) == len(survivors)
    for sid in survivors:
        verdict = stress.diagnostics[sid].stress_verdict
        baseline = stress.diagnostics[sid].reference_result
        assert verdict is not None and baseline is not None
        assert verdict.shock_summary is not None
        (cost,) = verdict.cost_results
        assert cost.cost_multiplier == 3.0
        assert cost.total_return <= baseline.return_metrics.total_return + 1e-9

    # Overfitting: evidence from WF, MC and regime all reached the final gate.
    for sid in survivors:
        analysis = by_name["overfit"].diagnostics[sid].overfitting_result
        assert analysis is not None
        assert analysis.indicators.mc_instability is not None
        assert analysis.indicators.regime_concentration is not None


def test_the_neighbour_stage_runs_on_real_simulations_each_under_an_id_of_its_own(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """The real runner, engine and database (plan R13): a strategy with one setting moved
    is a strategy the engine can run, and its run is stored under its own id. The
    runner stores a run's config under the run's strategy id, so a neighbour run under
    the id of the strategy it came from would overwrite that strategy's settings."""
    from sqlalchemy import select

    from autonomous_trading_platform.research.pipeline.pipeline_runner import (
        StagedPipelineConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
    from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
    from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs

    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner)
    raw_stages = _permissive_stage_dicts(exp)
    neighbours_raw = {
        "type": "neighbour",
        "name": "neighbours",
        "symbols": list(exp.symbols),
        "start_date": exp.start_date.isoformat(),
        "end_date": exp.end_date.isoformat(),
    }
    raw_stages.insert(len(raw_stages) - 1, neighbours_raw)
    stages = [StageRegistry.load(raw, ctx.simulation_runner) for raw in raw_stages]
    exp = replace(
        exp,
        strategy_set=_MINIMAL_STRATEGY_SET,
        staged_pipeline_config=StagedPipelineConfig(stages=stages, smoke=True),
    )

    result = ctx.experiment_orchestration_service.run_staged_experiment(exp)
    by_name = {sr.stage_name: sr for sr in result.stage_results}

    assert list(by_name) == ["sim", "wf", "mc", "regime", "stress", "neighbours", "overfit"]
    stage = by_name["neighbours"]
    survivors = {c.strategy_id: c for c in by_name["stress"].survivors}
    assert survivors, "permissive filters should let strategies reach the neighbour stage"
    # Report only: whoever came in goes on, and nobody else does.
    assert [c.strategy_id for c in stage.survivors] == list(survivors)
    assert [c.strategy_id for c in result.final_survivors] == list(survivors)

    neighbour_ids: set[str] = set()
    for sid, config in survivors.items():
        verdict = stage.diagnostics[sid].neighbour_verdict
        assert verdict is not None and verdict.enforced is False
        # Judged against the strategy's own run over the window, Monte Carlo's, reused.
        assert (
            stage.diagnostics[sid].reference_result
            is by_name["mc"].diagnostics[sid].reference_result
        )
        assert verdict.own_sharpe is not None and verdict.sharpe_needed is not None
        # Every setting was moved each way, and each move was run or says why not.
        settings = len(config.parameters)
        assert len(verdict.results) + len(verdict.skipped) == 2 * settings
        assert verdict.results, sid
        assert all(result.holds is not None for result in verdict.results)
        neighbour_ids |= {result.strategy_id for result in verdict.results}
    assert neighbour_ids.isdisjoint(survivors)
    assert {r.strategy_id for r in stage.simulation_results} == neighbour_ids

    # In the database: each survivor's stored settings are still its own...
    for sid, config in survivors.items():
        stored = db_session.get(StrategyConfigs, sid)
        assert stored is not None
        assert stored.config_json["parameters"] == config.parameters, sid
    # ...and the neighbours' runs are rows of their own, under their own ids.
    rows = db_session.scalars(
        select(SimulationRuns).where(SimulationRuns.window_role == "neighbour")
    ).all()
    assert {row.strategy_id for row in rows} == neighbour_ids
    assert {row.status for row in rows} == {"COMPLETED"}
    for neighbour_id in neighbour_ids:
        stored = db_session.get(StrategyConfigs, neighbour_id)
        assert stored is not None and stored.strategy_id == neighbour_id


def test_research_hook_runs_with_smoke_profile(
    smoke_data_root, pit_universe, db_session, replay_context, monkeypatch
) -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    monkeypatch.setattr(rh, "_replay_strategy_set", lambda *, smoke, seed: _MINIMAL_STRATEGY_SET)

    result = rh.run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        dataset_version_id=_DATASET_VERSION_ID,
        research_options={"profile": "smoke"},
    )

    assert result.status == "ok", result.errors
    assert result.summary["research_profile"] == "smoke"
    funnel = result.summary["stage_funnel"]
    assert next(iter(funnel)) == "initial_quality_filter"
    # Funnel counts never increase from one stage to the next.
    passed = [c["passed"] for c in funnel.values()]
    assert passed == sorted(passed, reverse=True)
    # Each stage is counted on what the stage before it passed on (walk-forward used to
    # count its verdicts, several per strategy).
    counts = list(funnel.values())
    for earlier, later in zip(counts, counts[1:], strict=False):
        assert later["entered"] == earlier["passed"]


def test_the_research_step_run_again_with_reuse_reads_its_runs_back(
    smoke_data_root, pit_universe, db_session, replay_context, monkeypatch
) -> None:
    """The whole research step twice on one database, as a resumed job runs it (plan
    R3): the second time every simulation of the pipeline is read back, and the step
    reports what it reported the first time. Everything the step writes besides its
    runs (the experiment, governance rows) is written a second time without harm."""
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
    from autonomous_trading_platform.research.simulation import completed_run_reuse

    monkeypatch.setattr(rh, "_replay_strategy_set", lambda *, smoke, seed: _MINIMAL_STRATEGY_SET)
    monkeypatch.setenv("GIT_SHA", "3" * 40)
    completed_run_reuse.code_version.cache_clear()

    def step(**kwargs):
        return rh.run_scheduled_research_at_timestamp(
            session=db_session,
            timestamp=_TICK_TS,
            replay_context=replay_context,
            dataset_version_id=_DATASET_VERSION_ID,
            research_options={"profile": "smoke"},
            **kwargs,
        )

    try:
        first = step()
        second = step(reuse_completed_runs=True)
    finally:
        completed_run_reuse.code_version.cache_clear()

    assert first.status == "ok", first.errors
    assert second.status == "ok", second.errors
    assert first.summary["total_runs"] > 0
    # Not asked for: the summary says so, and does not say "none were read back".
    assert first.summary["runs_reloaded"] is None
    assert second.summary["runs_reloaded"] == second.summary["total_runs"]
    for key in ("total_runs", "stage_funnel", "gate_report", "top_strategy_id", "cluster_count"):
        assert second.summary[key] == first.summary[key], key


def test_governance_source_run_is_the_full_window_run(
    smoke_data_root, pit_universe, db_session, replay_context, monkeypatch
) -> None:
    """Promotion reads its metrics from the run a governance record points at.
    Stage 1 covers the selection window only, so that run must be the
    full-window reference run the robustness stages recorded."""
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
    from autonomous_trading_platform.research.pipeline.pipeline_runner import (
        StagedPipelineConfig,
    )
    from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry

    build_real = rh._build_replay_experiment_definition
    built: dict = {}
    seeded: dict = {}

    def _build_permissive(**kwargs):
        exp = build_real(**kwargs)
        selection_end = exp.staged_pipeline_config.stages[0]._stage_config.end_date
        stages = [
            StageRegistry.load(raw, kwargs["simulation_runner"])
            for raw in _permissive_stage_dicts(exp, selection_end=selection_end)
        ]
        built["exp"] = replace(
            exp,
            strategy_set=_MINIMAL_STRATEGY_SET,
            staged_pipeline_config=StagedPipelineConfig(stages=stages, smoke=True),
        )
        built["selection_end"] = selection_end
        return built["exp"]

    monkeypatch.setattr(rh, "_build_replay_experiment_definition", _build_permissive)
    monkeypatch.setattr(
        rh, "_seed_research_governance_from_intelligence", lambda **kw: seeded.update(kw)
    )

    # Record what the scorer says is missing for each survivor it scores.
    from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
        CandidateRankingService,
    )

    score_real = CandidateRankingService._score_vector
    missing_evidence: list[tuple[str, ...]] = []

    def _score_and_record(self, vec, weights):
        out = score_real(self, vec, weights)
        missing_evidence.append(out[5])
        return out

    monkeypatch.setattr(CandidateRankingService, "_score_vector", _score_and_record)

    result = rh.run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        dataset_version_id=_DATASET_VERSION_ID,
        research_options={"profile": "smoke"},
    )

    assert result.status == "ok", result.errors
    exp = built["exp"]
    assert built["selection_end"] < exp.end_date
    assert seeded["config_by_id"], "permissive gates should leave final survivors"
    # Every survivor reaches the scorer with walk-forward and Monte Carlo evidence.
    # A survivor without both is not scored and can never be deployable (plan R5),
    # so a hook that stopped passing either would silently seed nothing.
    assert missing_evidence, "the hook should score its final survivors"
    assert all(missing == () for missing in missing_evidence), missing_evidence
    for sid in seeded["config_by_id"]:
        source = seeded["sim_by_id"][sid]
        assert (source.start_date, source.end_date) == (exp.start_date, exp.end_date)


def test_unknown_research_profile_falls_back_to_full() -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    assert rh._resolve_research_profile(None) == "full"
    assert rh._resolve_research_profile({"profile": "smoke"}) == "smoke"
    assert rh._resolve_research_profile({"profile": "turbo"}) == "full"


def test_research_lookback_is_a_setting_with_the_old_default() -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    assert rh._resolve_lookback_days(None) == rh.DEFAULT_RESEARCH_LOOKBACK_DAYS == 90
    assert rh._resolve_lookback_days({"lookback_days": 365}) == 365
    for bad in (0, -5, "90", 90.5, True):
        with pytest.raises(ValueError, match="lookback_days"):
            rh._resolve_lookback_days({"lookback_days": bad})


def test_research_universe_is_anchored_at_window_start(
    smoke_data_root, pit_universe, db_session, replay_context
) -> None:
    """A later rotation must not rewrite the symbols research tests on."""
    seed_universe_version(
        db_session, symbols=["AAPL"], effective_from=datetime(2023, 5, 1, tzinfo=UTC)
    )
    ctx = _build_context(db_session)
    exp = _replay_experiment(db_session, replay_context, ctx.simulation_runner)

    assert exp.start_date < date(2023, 5, 1) < exp.end_date
    # Window-start universe (all three), not the tick-date universe (AAPL only).
    assert sorted(exp.symbols) == sorted(_SYMBOLS)
    assert exp.universe_version == pit_universe


def test_research_refuses_to_run_without_point_in_time_universe(
    smoke_data_root, db_session, replay_context
) -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    result = rh.run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        dataset_version_id=_DATASET_VERSION_ID,
    )

    assert result.status == "failed"
    assert any("survivorship_guard" in e for e in result.errors)
