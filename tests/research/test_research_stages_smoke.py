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

    def _patched_build(*, session, universe_size=None):
        ctx = orig_build(session=session, universe_size=universe_size)
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
    assert mc._cfg.n_runs == 3
    assert stress._cfg.gate.cost_multipliers == (2.0,)
    assert all(item["options"]["n_samples"] == 2 for item in exp.strategy_set)

    full = _replay_experiment(db_session, replay_context, ctx.simulation_runner, profile="full")
    full_mc = next(s for s in full.staged_pipeline_config.stages if isinstance(s, MonteCarloStage))
    assert full_mc._cfg.n_runs == 5


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
    window = {
        "symbols": list(exp.symbols),
        "start_date": exp.start_date.isoformat(),
        "end_date": exp.end_date.isoformat(),
    }
    raw_stages = [
        {"type": "simulation", "name": "sim", "filter_config": _PERMISSIVE_FILTER, **window},
        {
            "type": "walk_forward",
            "name": "wf",
            "train_days": 45,
            "test_days": 30,
            "step_days": 15,
            "require_all_folds": False,
            "train_filter_config": _PERMISSIVE_FILTER,
            "test_filter_config": _PERMISSIVE_FILTER,
            **window,
        },
        {
            "type": "monte_carlo",
            "name": "mc",
            "n_runs": 3,
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
            "min_shock_survival_rate": 0.0,
            "min_cost_survival_rate": 0.0,
            **window,
        },
        {"type": "overfitting", "name": "overfit", "max_overfitting_probability": 1.0},
    ]
    stages = [StageRegistry.load(raw, ctx.simulation_runner) for raw in raw_stages]
    exp = replace(
        exp,
        strategy_set=_MINIMAL_STRATEGY_SET,
        staged_pipeline_config=StagedPipelineConfig(stages=stages),
    )

    result = ctx.experiment_orchestration_service.run_staged_experiment(exp)
    by_name = {sr.stage_name: sr for sr in result.stage_results}

    assert list(by_name) == ["sim", "wf", "mc", "regime", "stress", "overfit"]
    survivors = [c.strategy_id for c in by_name["mc"].survivors]
    assert survivors, "permissive filters should let strategies reach the robustness stages"
    assert [c.strategy_id for c in result.final_survivors] == survivors

    # Regime: labels classified from real bars; MC's representative run reused.
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


def test_research_hook_runs_with_smoke_profile(
    smoke_data_root, pit_universe, db_session, replay_context, monkeypatch
) -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    monkeypatch.setattr(rh, "_replay_strategy_set", lambda *, smoke: _MINIMAL_STRATEGY_SET)

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


def test_unknown_research_profile_falls_back_to_full() -> None:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    assert rh._resolve_research_profile(None) == "full"
    assert rh._resolve_research_profile({"profile": "smoke"}) == "smoke"
    assert rh._resolve_research_profile({"profile": "turbo"}) == "full"


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
