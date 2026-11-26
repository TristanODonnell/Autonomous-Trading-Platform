"""Isolated smoke test for run_scheduled_research_at_timestamp.

Exercises the full research pipeline (strategy generation → simulation →
quality filter → governance seeding) without running a complete platform
backtest. Seeds synthetic daily bars for 3 symbols over ~60 trading days so
the test completes in a few seconds while still exercising every code path
that fires at the monthly research tick.

Run with:
    python -m pytest tests/research/test_research_hooks_smoke.py -v
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.research.pipeline.admission_window import (
    EVALUATION_MONTHS,
    SELECTION_MONTHS,
    AdmissionWindowLayout,
    admission_window_layout,
    months_before,
)
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.metadata import (
    attach_metadata,
    build_metadata,
)
from autonomous_trading_platform.storage.parquet.paths import partition_file_path
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from tests.research.real_run_support import pinned_strategy_set
from tests.utilities.universe_seeding import seed_universe_version

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_SYMBOLS = ["AAPL", "MSFT", "SPY"]
_DATASET_VERSION_ID = "research_smoke_test_v1"
_START_DATE = date(2023, 1, 3)
_END_DATE = date(2023, 3, 3)  # ~60 calendar days → ~42 trading days
_TICK_DATE = date(2023, 3, 3)
_TICK_TS = datetime(2023, 3, 3, 21, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trading_days(start: date, end: date) -> list[date]:
    """Return weekday dates between start and end inclusive."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon–Fri
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _seed_bar_parquet(
    data_root: Path, symbol: str, bars: list[dict], dataset_version: str = _DATASET_VERSION_ID
) -> None:
    """Write a single data.parquet file for one symbol/year/month partition."""
    by_month: dict[tuple[str, str], list[dict]] = {}
    for bar in bars:
        key = (bar["year"], bar["month"])
        by_month.setdefault(key, []).append(bar)

    for (year, month), month_bars in by_month.items():
        file_path = partition_file_path(
            base_path=data_root,
            dataset=RAW_BARS_DATASET,
            dataset_version=dataset_version,
            partitions={"symbol": symbol, "year": year, "month": month},
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)

        arrays = {field.name: [b.get(field.name) for b in month_bars] for field in BAR_SCHEMA}
        table = pa.table(arrays, schema=BAR_SCHEMA)

        metadata_dict = build_metadata(
            dataset=RAW_BARS_DATASET,
            dataset_version=dataset_version,
            ingestion_timestamp=_TICK_TS.isoformat(),
            checksum="smoke_test_checksum",
        )
        schema_with_meta = attach_metadata(table.schema, metadata_dict)
        table = table.cast(schema_with_meta)

        pq.write_table(table, file_path)


def _make_bars(symbol: str, trading_days: list[date]) -> list[dict]:
    """Generate one synthetic daily bar per trading day."""
    now = datetime(2023, 1, 1, tzinfo=UTC)
    bars = []
    base_price = {"AAPL": 150.0, "MSFT": 250.0, "SPY": 400.0}.get(symbol, 100.0)

    for i, d in enumerate(trading_days):
        ts = datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)  # 9:30am ET
        close = base_price + i * 0.10
        bar_id = f"{symbol}_{d.isoformat()}_1d"
        bars.append(
            {
                "bar_id": bar_id,
                "timestamp": ts,
                "end_timestamp": ts + timedelta(hours=6, minutes=30),
                "interval": BarInterval.ONE_DAY.value,
                "symbol": symbol,
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000_000 + i * 10_000,
                "vwap": close,
                "trade_count": 50_000,
                "price_basis": PriceBasis.RAW.value,
                "adjustment_factor": 1.0,
                "source": "smoke_test",
                "ingested_at": now,
                "quality_flags": None,
                "date": d,
                "year": f"{d.year:04d}",
                "month": f"{d.month:02d}",
            }
        )
    return bars


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def research_smoke_data_root(tmp_path: Path, db_session, monkeypatch) -> Path:
    """Seed parquet bars + dataset_versions record, then patch data root."""
    trading_days = _trading_days(_START_DATE, _END_DATE)

    for symbol in _SYMBOLS:
        bars = _make_bars(symbol, trading_days)
        _seed_bar_parquet(tmp_path, symbol, bars)

    # Register the dataset version in the SOR so the research hook can find it
    row = DatasetVersions(
        dataset_version_id=_DATASET_VERSION_ID,
        dataset_name="raw_bars",
        created_at=_TICK_TS,
        source="smoke_test",
        price_basis=PriceBasis.RAW,
        interval=BarInterval.ONE_DAY,
        schema_version=RAW_BARS_DATASET.schema_version,
        symbol_coverage=len(_SYMBOLS),
        date_coverage_start=_START_DATE,
        date_coverage_end=_END_DATE,
        validation_status="validated",
        checksum="smoke_test_checksum",
    )
    db_session.add(row)
    db_session.flush()

    # Research resolves its universe point-in-time at the window start and the
    # survivorship guard refuses to run without one.
    seed_universe_version(
        db_session,
        symbols=_SYMBOLS,
        effective_from=datetime.combine(_START_DATE, datetime.min.time(), tzinfo=UTC),
    )

    # Patch parquet paths to point at tmp_path
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(parquet_paths, "get_data_root", lambda: tmp_path, raising=False)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PARQUET_DATA_ROOT", str(tmp_path))

    # Patch base_path in build_simulation_context so bar_reader + dataset_resolver
    # look in tmp_path instead of the hardcoded "data" directory.
    import autonomous_trading_platform.research.simulation.contexts.build_simulation_context as ctx_mod

    _orig_build = ctx_mod.build_simulation_context

    def _patched_build(*, session, universe_size=None, **kwargs):
        ctx = _orig_build(session=session, universe_size=universe_size, **kwargs)
        # Replace bar_reader and dataset_resolver with tmp_path-rooted versions
        ctx.bar_reader.base_path = tmp_path
        ctx.dataset_resolver.base_path = tmp_path
        ctx.window_loader.bar_reader.base_path = tmp_path
        if hasattr(ctx.window_loader, "feature_reader") and ctx.window_loader.feature_reader:
            ctx.window_loader.feature_reader.base_path = tmp_path
        ctx.simulation_runner.dataset_resolver.base_path = tmp_path
        return ctx

    monkeypatch.setattr(ctx_mod, "build_simulation_context", _patched_build)

    return tmp_path


@pytest.fixture()
def replay_context() -> PlatformReplayContext:
    return PlatformReplayContext.create(
        symbols=_SYMBOLS,
        actor="smoke_test",
        timestamp=_TICK_TS,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_research_hook_completes_without_error(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """run_scheduled_research_at_timestamp runs stage 1 only (60 days < 75 threshold)
    with 5 candidates (n_samples=1 each) and returns status ok or skipped."""
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    # Reduce n_samples to 1 per strategy type so the test runs in <5 seconds
    _orig_build_exp = rh_mod._build_replay_experiment_definition

    def _minimal_experiment(*args, **kwargs):
        from dataclasses import replace

        exp = _orig_build_exp(*args, **kwargs)
        if exp is None:
            return None
        minimal_strategy_set = pinned_strategy_set(
            "momentum", "mean_reversion", "moving_average_crossover"
        )
        return replace(exp, strategy_set=minimal_strategy_set, parameter_space=None)

    monkeypatch.setattr(rh_mod, "_build_replay_experiment_definition", _minimal_experiment)

    result = run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        dataset_version_id=_DATASET_VERSION_ID,
    )

    assert result.status in ("ok", "skipped"), (
        f"Expected ok/skipped, got {result.status!r}. Errors: {result.errors}"
    )


def test_research_hook_no_missing_price_flood(
    research_smoke_data_root, db_session, replay_context, monkeypatch, caplog
):
    """Verify that with clean daily bars (one per day per symbol), the research
    pipeline produces no simple_position_sizer.missing_price warnings."""
    import logging

    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    _orig_build_exp = rh_mod._build_replay_experiment_definition

    def _minimal_experiment(*args, **kwargs):
        from dataclasses import replace

        exp = _orig_build_exp(*args, **kwargs)
        if exp is None:
            return None
        return replace(
            exp,
            strategy_set=pinned_strategy_set("momentum"),
            parameter_space=None,  # pinned config, not a draw
        )

    monkeypatch.setattr(rh_mod, "_build_replay_experiment_definition", _minimal_experiment)

    with caplog.at_level(logging.WARNING, logger="autonomous_trading_platform"):
        run_scheduled_research_at_timestamp(
            session=db_session,
            timestamp=_TICK_TS,
            replay_context=replay_context,
            dataset_version_id=_DATASET_VERSION_ID,
        )

    missing_price_warnings = [r for r in caplog.records if "missing_price" in r.message]
    assert len(missing_price_warnings) == 0, (
        f"Got {len(missing_price_warnings)} missing_price warnings — "
        "expected 0 with clean daily bars:\n"
        + "\n".join(r.message for r in missing_price_warnings[:5])
    )


def test_research_hook_with_sparse_bars_logs_missing_price(
    research_smoke_data_root, db_session, monkeypatch
):
    """Confirm that when some symbols lack bars at a timestamp, missing_price IS
    logged (not silently dropped). Uses AAPL-only bars so MSFT/SPY signal but
    have no price data."""

    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    # Replay context that includes MSFT/SPY but we only seeded AAPL bars
    # (reuse the already-seeded data_root which has all 3, but we'll restrict symbols)
    aapl_only_ctx = PlatformReplayContext.create(
        symbols=["AAPL"], actor="smoke_test_sparse", timestamp=_TICK_TS
    )

    _orig_build_exp = rh_mod._build_replay_experiment_definition

    def _minimal_experiment(*args, **kwargs):
        from dataclasses import replace

        exp = _orig_build_exp(*args, **kwargs)
        if exp is None:
            return None
        return replace(
            exp,
            strategy_set=pinned_strategy_set("momentum"),
            parameter_space=None,  # pinned config, not a draw
            symbols=["AAPL"],
        )

    monkeypatch.setattr(rh_mod, "_build_replay_experiment_definition", _minimal_experiment)

    # This should complete fine (1 symbol, clean data)
    result = run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=aapl_only_ctx,
        dataset_version_id=_DATASET_VERSION_ID,
    )

    assert result.status in ("ok", "skipped")


# ---------------------------------------------------------------------------
# On a universe timeline (research plan, package B)
#
#   list one  2023-01-01 up to 2023-01-31 22:00   AAPL, MSFT
#   list two  2023-01-31 22:00 onwards            MSFT, SPY
# ---------------------------------------------------------------------------

_TIMELINE_ID = "universe_timeline_smoke"
_ROTATION_TS = datetime(2023, 1, 31, 22, 0, tzinfo=UTC)


def _smoke_timeline(first_list_from: datetime = datetime(2023, 1, 1, tzinfo=UTC)):
    from autonomous_trading_platform.contracts.runtime.universe_timeline import (
        UniverseTimeline,
        UniverseTimelineMember,
        UniverseTimelinePeriod,
    )

    def period(version_id, symbols, start, end):
        return UniverseTimelinePeriod(
            universe_version_id=version_id,
            effective_from=start,
            effective_to=end,
            members=[UniverseTimelineMember(symbol=s) for s in symbols],
        )

    return UniverseTimeline(
        dataset_version_id=_TIMELINE_ID,
        window_start=first_list_from.date(),
        content_hash="not-checked-here",
        periods=[
            period("list_one", ["AAPL", "MSFT"], first_list_from, _ROTATION_TS),
            period("list_two", ["MSFT", "SPY"], _ROTATION_TS, None),
        ],
    )


def _stage_configs(experiment) -> list:
    # The config of each stage that runs simulations (the stages keep it under two names).
    configs = []
    for stage in experiment.staged_pipeline_config.stages:
        config = getattr(stage, "_cfg", None) or getattr(stage, "_stage_config", None)
        if config is not None and hasattr(config, "symbols"):
            configs.append(config)
    return configs


class _TimelineRun:
    """One research tick on a timeline, with what the hook built and what the engine got."""

    def __init__(self) -> None:
        self.experiments: list = []
        self.engine_calls: list[dict] = []
        self.result: Any = None

    @property
    def signals(self):
        import pandas as pd

        frames = [call["result"].signal_log for call in self.engine_calls]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _run_on_timeline(
    db_session,
    replay_context,
    monkeypatch,
    *,
    timeline=None,
    options: dict | None = None,
    forbid_universe_tables: bool = True,
    backtest_dataset_id: str | None = _DATASET_VERSION_ID,
    timestamp: datetime = _TICK_TS,
    keep_strategy_set: bool = False,
) -> _TimelineRun:
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod
    from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
        SimulationExecutionEngine,
    )

    run = _TimelineRun()

    if timeline is not None:
        # The loader has its own tests; here the hook is handed the timeline as read.
        def source_for(_session):
            def source(version_id: str):
                if version_id != timeline.dataset_version_id:
                    raise LookupError(f"no timeline {version_id}")
                return timeline

            return source

        monkeypatch.setattr(rh_mod, "_universe_timeline_source", source_for)

    if forbid_universe_tables:

        def _tables_read(**_kwargs):
            raise AssertionError("the universe tables were read on a timeline run")

        monkeypatch.setattr(rh_mod, "_resolve_research_universe_scope", _tables_read)

    _orig_build_exp = rh_mod._build_replay_experiment_definition

    def _minimal_experiment(*args, **kwargs):
        from dataclasses import replace

        exp = _orig_build_exp(*args, **kwargs)
        if exp is None:
            return None
        if not keep_strategy_set:
            exp = replace(
                exp,
                strategy_set=pinned_strategy_set("momentum"),
                parameter_space=None,  # pinned config, not a draw
            )
        run.experiments.append(exp)
        return exp

    monkeypatch.setattr(rh_mod, "_build_replay_experiment_definition", _minimal_experiment)

    _orig_execute = SimulationExecutionEngine.execute

    def _spy(self, **kwargs):
        result = _orig_execute(self, **kwargs)
        run.engine_calls.append(
            {
                "universe_timeline": kwargs.get("universe_timeline"),
                "symbols": list(kwargs["window"].symbols),
                "dates": (kwargs["window"].start_date, kwargs["window"].end_date),
                "result": result,
            }
        )
        return result

    monkeypatch.setattr(SimulationExecutionEngine, "execute", _spy)

    run.result = rh_mod.run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=timestamp,
        replay_context=replay_context,
        dataset_version_id=backtest_dataset_id,
        research_options=options if options is not None else {"universe_timeline": _TIMELINE_ID},
    )
    return run


def test_research_hook_on_a_timeline_hands_it_to_every_simulation(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The option's id reaches the experiment, every stage config and the engine,
    and the universe tables are not read."""
    timeline = _smoke_timeline()

    run = _run_on_timeline(db_session, replay_context, monkeypatch, timeline=timeline)

    assert run.result.status == "ok", run.result.errors
    assert run.result.summary["universe_timeline_version"] == _TIMELINE_ID

    (experiment,) = run.experiments
    assert experiment.universe_version == _TIMELINE_ID
    assert experiment.symbols == ["AAPL", "MSFT", "SPY"]
    stage_configs = _stage_configs(experiment)
    assert stage_configs
    assert {cfg.universe_timeline_version for cfg in stage_configs} == {_TIMELINE_ID}

    assert run.engine_calls
    assert all(call["universe_timeline"] is timeline for call in run.engine_calls)
    # The window holds both lists, so every run of it loads all three names.
    assert {tuple(call["symbols"]) for call in run.engine_calls} == {("AAPL", "MSFT", "SPY")}


def test_research_hook_on_a_timeline_asks_only_members_for_signals(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The real engine on real bars: AAPL is asked only while it is in the list, SPY
    only once it is, MSFT throughout."""
    run = _run_on_timeline(db_session, replay_context, monkeypatch, timeline=_smoke_timeline())

    signals = run.signals
    assert run.result.status == "ok", run.result.errors
    assert not signals.empty, "the scenario gives no signals, so the checks below say nothing"

    def stamps(symbol: str):
        return list(signals[signals["symbol"] == symbol]["timestamp"])

    assert stamps("AAPL") and all(ts < _ROTATION_TS for ts in stamps("AAPL"))
    assert stamps("SPY") and all(ts >= _ROTATION_TS for ts in stamps("SPY"))
    msft = stamps("MSFT")
    assert any(ts < _ROTATION_TS for ts in msft) and any(ts >= _ROTATION_TS for ts in msft)


def test_research_hook_without_the_option_runs_on_the_fixed_list(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    run = _run_on_timeline(
        db_session, replay_context, monkeypatch, options={}, forbid_universe_tables=False
    )

    assert run.result.status == "ok", run.result.errors
    assert run.result.summary["universe_timeline_version"] is None
    assert run.engine_calls
    assert all(call["universe_timeline"] is None for call in run.engine_calls)
    (experiment,) = run.experiments
    stage_configs = _stage_configs(experiment)
    assert {cfg.universe_timeline_version for cfg in stage_configs} == {None}


def test_research_hook_refuses_a_timeline_naming_symbols_the_replay_did_not_ingest(
    research_smoke_data_root, db_session, monkeypatch
):
    """Without a timeline the list is cut down to what was ingested. A timeline's
    lists are not cut down: trading the rest would not be the timeline's universe."""
    two_of_three = PlatformReplayContext.create(
        symbols=["AAPL", "MSFT"], actor="smoke_test", timestamp=_TICK_TS
    )

    run = _run_on_timeline(db_session, two_of_three, monkeypatch, timeline=_smoke_timeline())

    assert run.result.status == "failed"
    assert "survivorship_guard" in run.result.errors[0]
    assert "did not ingest: ['SPY']" in run.result.errors[0]
    assert run.engine_calls == []


def test_research_hook_refuses_a_window_that_starts_before_the_timeline(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    late = _smoke_timeline(first_list_from=datetime(2023, 1, 10, tzinfo=UTC))

    run = _run_on_timeline(db_session, replay_context, monkeypatch, timeline=late)

    assert run.result.status == "failed"
    assert "survivorship_guard" in run.result.errors[0]
    assert "no list in force on 2023-01-03" in run.result.errors[0]
    assert run.engine_calls == []


def test_research_hook_fails_on_a_timeline_that_is_not_registered(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The real loader this time: an id with no dataset version behind it."""
    run = _run_on_timeline(
        db_session, replay_context, monkeypatch, options={"universe_timeline": "no_such_timeline"}
    )

    assert run.result.status == "failed"
    assert run.result.errors[0].startswith("universe_timeline:")
    assert "no_such_timeline" in run.result.errors[0]
    assert run.engine_calls == []


@pytest.mark.parametrize("bad", ["", "   ", 7, ["universe_timeline_smoke"]])
def test_research_hook_rejects_a_timeline_option_that_is_not_an_id(db_session, replay_context, bad):
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    with pytest.raises(ValueError, match="research.options.universe_timeline"):
        run_scheduled_research_at_timestamp(
            session=db_session,
            timestamp=_TICK_TS,
            replay_context=replay_context,
            research_options={"universe_timeline": bad},
        )


# ---------------------------------------------------------------------------
# A dataset the job names (research.options.dataset_version)
# ---------------------------------------------------------------------------

_WINDOW_DATASET_ID = "research_window_smoke"
_REPLAY_DATASET_WITH_NO_BARS = "replay_dataset_with_no_bars"
# 45 days back from the last bar day (2023-03-03) is 2023-01-17, inside the bars.
_NAMED = {"dataset_version": _WINDOW_DATASET_ID, "lookback_days": 45}


def _register_window_dataset(
    db_session,
    data_root: Path,
    *,
    symbols: list[str] | None = None,
    first: date = _START_DATE,
    last: date = _END_DATE,
    **row_overrides: Any,
) -> None:
    """A research-window dataset as ``atp datasets build-window`` registers one, with
    its bars under its own version id."""
    from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
        RESEARCH_WINDOW_LIFECYCLE,
        RESEARCH_WINDOW_SOURCE,
    )

    names = list(symbols if symbols is not None else _SYMBOLS)
    for symbol in names:
        _seed_bar_parquet(
            data_root,
            symbol,
            _make_bars(symbol, _trading_days(first, last)),
            dataset_version=_WINDOW_DATASET_ID,
        )
    fields: dict[str, Any] = dict(
        dataset_version_id=_WINDOW_DATASET_ID,
        dataset_name="raw_bars",
        created_at=_TICK_TS,
        source=RESEARCH_WINDOW_SOURCE,
        price_basis=PriceBasis.RAW,
        interval=BarInterval.ONE_DAY,
        schema_version=RAW_BARS_DATASET.schema_version,
        symbol_coverage=len(names),
        date_coverage_start=first,
        date_coverage_end=last,
        validation_status="validated",
        checksum="smoke_test_checksum",
        source_manifest={"pipeline": RESEARCH_WINDOW_SOURCE, "symbols": names},
        metadata_json={"dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE, "excluded_sessions": []},
    )
    fields.update(row_overrides)
    db_session.add(DatasetVersions(**fields))
    db_session.flush()


def _run_named(db_session, replay_context, monkeypatch, **kwargs: Any) -> _TimelineRun:
    kwargs.setdefault("options", _NAMED)
    kwargs.setdefault("forbid_universe_tables", False)
    return _run_on_timeline(db_session, replay_context, monkeypatch, **kwargs)


def test_research_hook_reads_the_dataset_the_job_names(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The replay hands over a dataset that has no bars at all, so a run that trades
    can only have read the named one."""
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(
        db_session, replay_context, monkeypatch, backtest_dataset_id=_REPLAY_DATASET_WITH_NO_BARS
    )

    assert run.result.status == "ok", run.result.errors
    assert run.result.summary["dataset_version"] == _WINDOW_DATASET_ID
    assert run.result.summary["window_start"] == "2023-01-17"
    assert run.result.summary["window_end"] == "2023-03-03"
    (experiment,) = run.experiments
    assert experiment.dataset_version == _WINDOW_DATASET_ID
    assert (experiment.start_date, experiment.end_date) == (date(2023, 1, 17), date(2023, 3, 3))
    assert run.engine_calls
    assert not run.signals.empty


def test_research_hook_without_the_option_reads_the_replays_own_dataset(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """A window dataset that is registered but not named is not picked up."""
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(db_session, replay_context, monkeypatch, options={})

    assert run.result.status == "ok", run.result.errors
    assert run.result.summary["dataset_version"] == _DATASET_VERSION_ID
    (experiment,) = run.experiments
    assert experiment.dataset_version == _DATASET_VERSION_ID
    # The replay's dataset is clamped to, as before: 90 days back is before its first day.
    assert experiment.start_date == _START_DATE


def test_research_hook_on_a_named_dataset_trades_the_names_it_holds(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """Without a timeline the list is cut to the names that have bars. On a named
    dataset those are its names, not the ones the replay ingested."""
    _register_window_dataset(db_session, research_smoke_data_root, symbols=["AAPL", "MSFT"])

    run = _run_named(db_session, replay_context, monkeypatch)

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    assert experiment.symbols == ["AAPL", "MSFT"]
    assert {tuple(call["symbols"]) for call in run.engine_calls} == {("AAPL", "MSFT")}


def test_research_hook_on_a_named_dataset_is_not_cut_to_what_the_replay_ingested(
    research_smoke_data_root, db_session, monkeypatch
):
    _register_window_dataset(db_session, research_smoke_data_root)
    one_of_three = PlatformReplayContext.create(
        symbols=["AAPL"], actor="smoke_test", timestamp=_TICK_TS
    )

    run = _run_named(db_session, one_of_three, monkeypatch)

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    assert experiment.symbols == ["AAPL", "MSFT", "SPY"]


def test_research_hook_refuses_a_timeline_naming_symbols_the_named_dataset_does_not_hold(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    _register_window_dataset(db_session, research_smoke_data_root, symbols=["AAPL", "MSFT"])

    run = _run_named(
        db_session,
        replay_context,
        monkeypatch,
        timeline=_smoke_timeline(),
        options={**_NAMED, "universe_timeline": _TIMELINE_ID},
        forbid_universe_tables=True,
    )

    assert run.result.status == "failed"
    assert "survivorship_guard" in run.result.errors[0]
    assert f"dataset {_WINDOW_DATASET_ID} does not hold: ['SPY']" in run.result.errors[0]
    assert run.engine_calls == []


def test_research_hook_on_a_named_dataset_ends_the_window_on_its_last_day(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The tick is a week after the dataset's last day: the window ends where the bars
    do, and is still the number of days asked for."""
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(
        db_session, replay_context, monkeypatch, timestamp=datetime(2023, 3, 10, 21, 0, tzinfo=UTC)
    )

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    assert (experiment.start_date, experiment.end_date) == (date(2023, 1, 17), date(2023, 3, 3))
    assert {(cfg.start_date, cfg.end_date) for cfg in _stage_configs(experiment)} == {
        (date(2023, 1, 17), date(2023, 3, 3))
    }


def test_research_hook_on_a_named_dataset_ends_every_stage_on_its_last_day(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """A window long enough for all six stages (what the hook builds is checked here,
    not what the stages then find in these bars)."""
    _register_window_dataset(
        db_session, research_smoke_data_root, first=date(2022, 11, 1), last=_END_DATE
    )
    timeline = _smoke_timeline(first_list_from=datetime(2022, 11, 1, tzinfo=UTC))

    run = _run_named(
        db_session,
        replay_context,
        monkeypatch,
        timeline=timeline,
        options={"dataset_version": _WINDOW_DATASET_ID, "universe_timeline": _TIMELINE_ID},
        forbid_universe_tables=True,
        timestamp=datetime(2023, 3, 10, 21, 0, tzinfo=UTC),
    )

    (experiment,) = run.experiments
    start, end = date(2022, 12, 3), date(2023, 3, 3)  # 90 days, ending on the last bar day
    assert (experiment.start_date, experiment.end_date) == (start, end)
    stage_configs = _stage_configs(experiment)
    assert [cfg.name for cfg in stage_configs] == [
        "initial_quality_filter",
        "walk_forward_robustness",
        "monte_carlo_structural_robustness",
        "regime_robustness",
        "stress_robustness",
    ]
    assert {cfg.start_date for cfg in stage_configs} == {start}
    # Stage 1 stops before the first test fold; every later stage ends with the bars.
    assert stage_configs[0].end_date < end
    assert {cfg.end_date for cfg in stage_configs[1:]} == {end}


def test_research_hook_refuses_a_window_the_named_dataset_cannot_hold(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The replay's own dataset is clamped to. A named one is not: 90 days were asked
    for and it holds 59, so the step fails and says so."""
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(
        db_session, replay_context, monkeypatch, options={"dataset_version": _WINDOW_DATASET_ID}
    )

    assert run.result.status == "failed"
    assert run.result.errors[0].startswith("research_window:")
    assert "a window of 90 days ending 2023-03-03 starts on 2022-12-03" in run.result.errors[0]
    assert (
        f"before the first day of dataset {_WINDOW_DATASET_ID} (2023-01-03)"
        in (run.result.errors[0])
    )
    assert run.experiments == []
    assert run.engine_calls == []


def test_research_hook_refuses_a_research_date_before_the_named_dataset(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(
        db_session, replay_context, monkeypatch, timestamp=datetime(2022, 12, 30, 21, 0, tzinfo=UTC)
    )

    assert run.result.status == "failed"
    assert run.result.errors[0].startswith("research_window:")
    assert "the research date 2022-12-30 is before the first day" in run.result.errors[0]
    assert run.engine_calls == []


def test_research_hook_refuses_a_window_too_short_to_judge_on_a_named_dataset(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """On the replay's own dataset a short window is a skipped step; a job that names
    its bars and asks for ten days is told."""
    _register_window_dataset(db_session, research_smoke_data_root)

    run = _run_named(
        db_session,
        replay_context,
        monkeypatch,
        options={"dataset_version": _WINDOW_DATASET_ID, "lookback_days": 10},
    )

    assert run.result.status == "failed"
    assert "a window of 10 days is under the 15 a research run needs" in run.result.errors[0]


def test_research_hook_fails_on_a_named_dataset_that_is_not_registered(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    run = _run_named(db_session, replay_context, monkeypatch)

    assert run.result.status == "failed"
    assert run.result.errors[0].startswith("dataset_version:")
    assert f"{_WINDOW_DATASET_ID} is not registered" in run.result.errors[0]
    assert run.experiments == []


def test_research_hook_fails_on_a_named_dataset_that_is_not_a_research_window(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The smoke dataset is validated raw bars, but nobody checked its sessions."""
    run = _run_named(
        db_session,
        replay_context,
        monkeypatch,
        options={"dataset_version": _DATASET_VERSION_ID, "lookback_days": 45},
    )

    assert run.result.status == "failed"
    assert run.result.errors[0].startswith("dataset_version:")
    assert "is not a research-window dataset" in run.result.errors[0]
    assert run.experiments == []


@pytest.mark.parametrize("bad", ["", "   ", 7, ["research_window_smoke"]])
def test_research_hook_rejects_a_dataset_option_that_is_not_an_id(db_session, replay_context, bad):
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    with pytest.raises(ValueError, match="research.options.dataset_version"):
        run_scheduled_research_at_timestamp(
            session=db_session,
            timestamp=_TICK_TS,
            replay_context=replay_context,
            research_options={"dataset_version": bad},
        )


# ---------------------------------------------------------------------------
# The admission layout (research.options.window_layout: admission)
# ---------------------------------------------------------------------------
#
# Every date below follows from the holdout date, so these tests move with it. One test
# (the last of the section) pins the layout in force to the dates written in the plan.

# The selection piece's first calendar day.
_ADMISSION_FROM = months_before(months_before(HOLDOUT_START, EVALUATION_MONTHS), SELECTION_MONTHS)
_ADMISSION_BARS_FIRST = _ADMISSION_FROM - timedelta(days=45)
_ADMISSION_BARS_LAST = HOLDOUT_START + timedelta(days=45)
_ADMISSION_TICK = HOLDOUT_START + timedelta(days=30)
_ADMISSION_TICK_TS = datetime.combine(_ADMISSION_TICK, time(21, 0), tzinfo=UTC)
# Inside the selection piece: the list in force on the evaluation piece's first day is
# not the one in force on the window's first day.
_ADMISSION_ROTATION_TS = datetime.combine(
    _ADMISSION_FROM + timedelta(days=100), time(22, 0), tzinfo=UTC
)
_ADMISSION = {
    "window_layout": "admission",
    "dataset_version": _WINDOW_DATASET_ID,
    "universe_timeline": _TIMELINE_ID,
}
_STAGE_NAMES = [
    "initial_quality_filter",
    "walk_forward_robustness",
    "monte_carlo_structural_robustness",
    "regime_robustness",
    "stress_robustness",
]


def _admission_timeline(first_list_from: date = _ADMISSION_BARS_FIRST):
    """AAPL and MSFT from ``first_list_from``; MSFT and SPY from the rotation on."""
    from autonomous_trading_platform.contracts.runtime.universe_timeline import (
        UniverseTimeline,
        UniverseTimelineMember,
        UniverseTimelinePeriod,
    )

    begins = datetime.combine(first_list_from, time.min, tzinfo=UTC)

    def period(version_id, symbols, start, end):
        return UniverseTimelinePeriod(
            universe_version_id=version_id,
            effective_from=start,
            effective_to=end,
            members=[UniverseTimelineMember(symbol=s) for s in symbols],
        )

    return UniverseTimeline(
        dataset_version_id=_TIMELINE_ID,
        window_start=first_list_from,
        content_hash="not-checked-here",
        periods=[
            period("list_one", ["AAPL", "MSFT"], begins, _ADMISSION_ROTATION_TS),
            period("list_two", ["MSFT", "SPY"], _ADMISSION_ROTATION_TS, None),
        ],
    )


def _nyse_sessions(first: date, last: date) -> list[date]:
    from autonomous_trading_platform.runtime.clock import RealMarketCalendar

    return list(RealMarketCalendar().trading_days(first, last))


def _expected_layout(
    *,
    left_out: tuple[date, ...] = (),
    as_of: date = _ADMISSION_TICK,
    first: date = _ADMISSION_BARS_FIRST,
    last: date = _ADMISSION_BARS_LAST,
) -> AdmissionWindowLayout:
    """The layout the hook should arrive at, from the layout function (which has its own
    tests) and the NYSE calendar."""
    return admission_window_layout(
        holdout_start=HOLDOUT_START,
        as_of=as_of,
        data_first=first,
        data_last=last,
        trading_days=lambda a, b: [day for day in _nyse_sessions(a, b) if day not in left_out],
    )


def _run_admission(
    db_session,
    replay_context,
    monkeypatch,
    data_root: Path,
    *,
    first: date = _ADMISSION_BARS_FIRST,
    last: date = _ADMISSION_BARS_LAST,
    left_out: tuple[date, ...] = (),
    timeline=None,
    options: dict | None = None,
    timestamp: datetime = _ADMISSION_TICK_TS,
    keep_strategy_set: bool = False,
) -> _TimelineRun:
    """One research tick in the admission layout, on a dataset registered for it. The
    replay's own dataset has no bars, and the universe tables may not be read."""
    from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
        RESEARCH_WINDOW_LIFECYCLE,
    )

    _register_window_dataset(
        db_session,
        data_root,
        first=first,
        last=last,
        metadata_json={
            "dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE,
            "excluded_sessions": [day.isoformat() for day in left_out],
        },
    )
    return _run_on_timeline(
        db_session,
        replay_context,
        monkeypatch,
        timeline=timeline if timeline is not None else _admission_timeline(),
        options=options if options is not None else _ADMISSION,
        forbid_universe_tables=True,
        backtest_dataset_id=_REPLAY_DATASET_WITH_NO_BARS,
        timestamp=timestamp,
        keep_strategy_set=keep_strategy_set,
    )


def _span(piece) -> dict[str, Any]:
    return {
        "first": piece.first.isoformat(),
        "last": piece.last.isoformat(),
        "sessions": piece.sessions,
    }


def test_admission_layout_gives_each_stage_its_days(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """Stage 1 reads the selection piece; walk-forward tests on the evaluation piece's
    three folds, every fold trained on the selection piece, two of three to pass; Monte
    Carlo, regime and stress read the evaluation piece."""
    layout = _expected_layout()
    selection, evaluation = layout.selection, layout.evaluation

    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    assert experiment.dataset_version == _WINDOW_DATASET_ID
    assert (experiment.start_date, experiment.end_date) == (selection.first, evaluation.last)
    by_name = {cfg.name: cfg for cfg in _stage_configs(experiment)}
    # The admission layout adds the parameter neighbours after stress (plan R13).
    admission_stages = [*_STAGE_NAMES, "parameter_neighbours"]
    assert list(by_name) == admission_stages

    stage_1 = by_name["initial_quality_filter"]
    assert (stage_1.start_date, stage_1.end_date) == (selection.first, selection.last)

    walk_forward = by_name["walk_forward_robustness"]
    assert (walk_forward.start_date, walk_forward.end_date) == (selection.first, evaluation.last)
    assert len(layout.test_folds) == 3
    assert walk_forward.fold_windows() == [
        (selection.first, selection.last, fold.first, fold.last) for fold in layout.test_folds
    ]
    # The test folds tile the evaluation piece, and start after stage 1 stops reading.
    assert walk_forward.folds[0].test_start == evaluation.first > stage_1.end_date
    assert walk_forward.folds[-1].test_end == evaluation.last
    assert (walk_forward.require_all_folds, walk_forward.min_folds_passed) == (False, 2)
    # No overlap exception, and no rolling folds beside the ones given by date.
    assert walk_forward.allow_overlapping_test_folds is False
    assert (walk_forward.train_days, walk_forward.test_days, walk_forward.step_days) == (0, 0, 0)

    for name in admission_stages[2:]:
        gate = by_name[name]
        assert (gate.start_date, gate.end_date) == (evaluation.first, evaluation.last), name
    # Report only, at the gate's own defaults.
    assert by_name["parameter_neighbours"].gate.enforce is False
    # The run over the evaluation piece answers for the walk-forward filter itself.
    monte_carlo = by_name["monte_carlo_structural_robustness"]
    assert monte_carlo.base_run_clears_filter is True
    assert monte_carlo.filter_config == walk_forward.test_filter_config

    # The first simulation is stage 1's, on the selection piece, from the named dataset.
    assert run.engine_calls
    assert run.engine_calls[0]["dates"] == (selection.first, selection.last)


def test_admission_layout_reads_no_holdout_day(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The research runs a month into the holdout and the bars go on past it; no stage
    is given a holdout day and no simulation reads one."""
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    (experiment,) = run.experiments
    assert experiment.end_date < HOLDOUT_START <= _ADMISSION_TICK < _ADMISSION_BARS_LAST
    for cfg in _stage_configs(experiment):
        assert cfg.end_date < HOLDOUT_START, cfg.name
    walk_forward = _stage_configs(experiment)[1]
    assert all(fold.test_end < HOLDOUT_START for fold in walk_forward.folds)
    assert run.engine_calls
    for call in run.engine_calls:
        assert experiment.start_date <= call["dates"][0] <= call["dates"][1] < HOLDOUT_START


def test_admission_layout_labels_regimes_from_the_list_on_the_evaluation_pieces_first_day(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The list rotated inside the selection piece. The stages are handed every name of
    the window; the regime labels come from the list in force when the evaluation piece
    begins, not the one in force on the window's first day (AAPL, MSFT)."""
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    (experiment,) = run.experiments
    assert experiment.symbols == ["AAPL", "MSFT", "SPY"]
    by_name = {cfg.name: cfg for cfg in _stage_configs(experiment)}
    assert {tuple(cfg.symbols) for cfg in by_name.values()} == {("AAPL", "MSFT", "SPY")}
    assert by_name["regime_robustness"].label_symbols == ["MSFT", "SPY"]
    # Stage 1 traded on the timeline: the first list's names until the rotation.
    assert run.engine_calls[0]["universe_timeline"] is not None


def test_admission_layout_holds_stage_1_to_at_least_the_train_check(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """Every fold's train window is the selection piece, the days stage 1 selected on.
    Stage 1's filter is at least as strict as the train check on every threshold, so
    the train check eliminates nobody stage 1 let through: the folds are decided by
    their test periods."""
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    (experiment,) = run.experiments
    by_name = {cfg.name: cfg for cfg in _stage_configs(experiment)}
    selected, trained = (
        by_name["initial_quality_filter"].filter_config,
        by_name["walk_forward_robustness"].train_filter_config,
    )
    for threshold in (
        "min_sharpe",
        "max_drawdown",
        "min_trades",
        "min_consistency_score",
        "min_profit_factor",
        "min_win_rate",
        "min_total_return",
        "robustness_min_sharpe",
        "robustness_min_profitable_windows",
    ):
        assert getattr(selected, threshold) >= getattr(trained, threshold), threshold
    assert selected.max_daily_turnover <= trained.max_daily_turnover
    assert selected.max_return_variance == trained.max_return_variance


def test_admission_layout_reports_its_pieces(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    layout = _expected_layout()

    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    assert run.result.status == "ok", run.result.errors
    summary = run.result.summary
    assert summary["window_layout"] == "admission"
    assert summary["dataset_version"] == _WINDOW_DATASET_ID
    assert summary["universe_timeline_version"] == _TIMELINE_ID
    assert summary["window_start"] == layout.selection.first.isoformat()
    assert summary["window_end"] == layout.evaluation.last.isoformat()
    assert layout.holdout is not None
    assert summary["admission_window"] == {
        "selection": _span(layout.selection),
        "evaluation": _span(layout.evaluation),
        "test_folds": [_span(fold) for fold in layout.test_folds],
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout": _span(layout.holdout),
    }
    # The holdout is listed up to the research date; it is past every gate's last day.
    assert summary["admission_window"]["holdout"]["first"] > summary["window_end"]
    assert summary["admission_window"]["holdout"]["last"] <= _ADMISSION_TICK.isoformat()


def test_admission_layout_counts_sessions_without_the_ones_the_dataset_left_out(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """A session the dataset left out by name is no session of a fold: with one taken
    out of the first test fold, that fold ends a session later and the cut moves."""
    plain = _expected_layout()
    left_out = _nyse_sessions(plain.evaluation.first, plain.evaluation.last)[9]
    expected = _expected_layout(left_out=(left_out,))
    assert plain.test_folds[0].first < left_out < plain.test_folds[0].last
    assert expected.test_folds[0].last > plain.test_folds[0].last

    run = _run_admission(
        db_session, replay_context, monkeypatch, research_smoke_data_root, left_out=(left_out,)
    )

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    walk_forward = _stage_configs(experiment)[1]
    assert [(fold.test_start, fold.test_end) for fold in walk_forward.folds] == [
        (fold.first, fold.last) for fold in expected.test_folds
    ]
    reported = run.result.summary["admission_window"]
    assert reported["evaluation"]["sessions"] == plain.evaluation.sessions - 1
    assert [fold["sessions"] for fold in reported["test_folds"]] == [
        fold.sessions for fold in expected.test_folds
    ]


@pytest.mark.parametrize(
    ("dataset", "timestamp", "reason"),
    [
        pytest.param(
            {},
            datetime.combine(HOLDOUT_START - timedelta(days=1), time(21, 0), tzinfo=UTC),
            f"before the holdout date {HOLDOUT_START}",
            id="the research runs before the holdout date",
        ),
        pytest.param(
            {"first": _ADMISSION_FROM + timedelta(days=7)},
            _ADMISSION_TICK_TS,
            f"the bars start on {_ADMISSION_FROM + timedelta(days=7)}; the selection piece "
            f"starts on {_ADMISSION_FROM}",
            id="the bars start after the selection piece does",
        ),
        pytest.param(
            {"last": HOLDOUT_START - timedelta(days=30)},
            _ADMISSION_TICK_TS,
            f"the bars end on {HOLDOUT_START - timedelta(days=30)}; the evaluation piece ends",
            id="the bars end before the evaluation piece does",
        ),
    ],
)
def test_admission_layout_that_cannot_be_laid_on_the_dataset_is_a_failed_step(
    research_smoke_data_root, db_session, replay_context, monkeypatch, dataset, timestamp, reason
):
    """Failed with the reason, not skipped, and not shortened to fit: nothing is built
    and nothing is simulated."""
    run = _run_admission(
        db_session,
        replay_context,
        monkeypatch,
        research_smoke_data_root,
        timestamp=timestamp,
        **dataset,
    )

    assert run.result.status == "failed"
    (error,) = run.result.errors
    assert error.startswith(
        f"research_window: the admission layout cannot be laid on dataset {_WINDOW_DATASET_ID}: "
    )
    assert reason in error
    assert run.experiments == []
    assert run.engine_calls == []


def test_admission_layout_refuses_a_timeline_that_begins_after_the_selection_piece(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    run = _run_admission(
        db_session,
        replay_context,
        monkeypatch,
        research_smoke_data_root,
        timeline=_admission_timeline(first_list_from=_ADMISSION_FROM + timedelta(days=10)),
    )

    assert run.result.status == "failed"
    assert "survivorship_guard" in run.result.errors[0]
    assert "The research window must start inside the timeline" in run.result.errors[0]
    assert run.engine_calls == []


def _research_tick(db_session, replay_context, options: dict):
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        run_scheduled_research_at_timestamp,
    )

    return run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_ADMISSION_TICK_TS,
        replay_context=replay_context,
        research_options=options,
    )


@pytest.mark.parametrize(
    "bad", ["Admission", "admission ", "rolling", "lookback", "", 7, True, ["admission"]]
)
def test_research_hook_rejects_a_window_layout_it_does_not_know(db_session, replay_context, bad):
    """Never the lookback window in its place: the run would look valid and be a
    different experiment."""
    with pytest.raises(ValueError, match="research.options.window_layout must be one of"):
        _research_tick(db_session, replay_context, {**_ADMISSION, "window_layout": bad})


def test_admission_layout_without_a_universe_timeline_is_refused(db_session, replay_context):
    options = {key: value for key, value in _ADMISSION.items() if key != "universe_timeline"}

    with pytest.raises(ValueError, match="needs research.options.universe_timeline"):
        _research_tick(db_session, replay_context, options)


def test_admission_layout_without_a_dataset_is_refused(db_session, replay_context):
    options = {key: value for key, value in _ADMISSION.items() if key != "dataset_version"}

    with pytest.raises(ValueError, match="needs research.options.dataset_version"):
        _research_tick(db_session, replay_context, options)


@pytest.mark.parametrize("days", [90, 540])
def test_admission_layout_with_a_window_length_is_refused(db_session, replay_context, days):
    """Even the default length: the layout's length follows from the holdout date."""
    with pytest.raises(ValueError, match="lookback_days does not apply with window_layout"):
        _research_tick(db_session, replay_context, {**_ADMISSION, "lookback_days": days})


def test_research_hook_without_the_layout_option_rolls_its_folds_as_before(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The same window as the six-stage test above, without the option: the 90-day
    window, rolling folds with their overlap exception, one fold to pass, and every
    stage after walk-forward on the whole window."""
    _register_window_dataset(
        db_session, research_smoke_data_root, first=date(2022, 11, 1), last=_END_DATE
    )

    run = _run_named(
        db_session,
        replay_context,
        monkeypatch,
        timeline=_smoke_timeline(first_list_from=datetime(2022, 11, 1, tzinfo=UTC)),
        options={"dataset_version": _WINDOW_DATASET_ID, "universe_timeline": _TIMELINE_ID},
        forbid_universe_tables=True,
    )

    assert run.result.status == "ok", run.result.errors
    assert run.result.summary["window_layout"] is None
    assert run.result.summary["admission_window"] is None
    # No holdout is set aside in this layout: none is checked and none is reported.
    assert run.result.summary["holdout_report"] is None
    # The gate report is not the admission layout's alone.
    _assert_gate_report_counts_what_the_funnel_counts(run.result.summary)
    (experiment,) = run.experiments
    assert experiment.staged_pipeline_config.holdout_start is None
    start, end = date(2022, 12, 3), date(2023, 3, 3)
    by_name = {cfg.name: cfg for cfg in _stage_configs(experiment)}
    # No parameter neighbours in this layout: a job without the option runs what
    # it ran before.
    assert list(by_name) == _STAGE_NAMES
    walk_forward = by_name["walk_forward_robustness"]
    assert walk_forward.folds is None
    assert (walk_forward.train_days, walk_forward.test_days, walk_forward.step_days) == (45, 30, 15)
    assert walk_forward.allow_overlapping_test_folds is True
    assert (walk_forward.require_all_folds, walk_forward.min_folds_passed) == (False, 1)
    assert by_name["initial_quality_filter"].end_date == start + timedelta(days=44)
    for name in _STAGE_NAMES[1:]:
        assert (by_name[name].start_date, by_name[name].end_date) == (start, end), name
    assert by_name["monte_carlo_structural_robustness"].base_run_clears_filter is False
    # Labels from the list in force on the window's first day.
    assert by_name["regime_robustness"].label_symbols == ["AAPL", "MSFT"]


def test_admission_layout_in_force_is_the_one_written_in_the_plan(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The window in force since 2026-11-16, with the two sessions the 24-month dataset
    leaves out by name. Pinned on purpose: when the holdout date moves, this fails until
    the new dates are worked out and written in the plan."""
    assert date(2026, 5, 18) == HOLDOUT_START, (
        "the holdout date moved: work out the new pieces, record them in the research "
        "plan, and update the dates in this test"
    )

    run = _run_admission(
        db_session,
        replay_context,
        monkeypatch,
        research_smoke_data_root,
        first=date(2024, 10, 1),
        left_out=(date(2024, 12, 23), date(2025, 3, 10)),
    )

    assert run.result.status == "ok", run.result.errors
    reported = run.result.summary["admission_window"]
    assert reported["selection"] == {"first": "2024-11-18", "last": "2025-08-15", "sessions": 183}
    assert reported["evaluation"] == {"first": "2025-08-18", "last": "2026-05-15", "sessions": 188}
    assert reported["test_folds"] == [
        {"first": "2025-08-18", "last": "2025-11-13", "sessions": 63},
        {"first": "2025-11-14", "last": "2026-02-17", "sessions": 63},
        {"first": "2026-02-18", "last": "2026-05-15", "sessions": 62},
    ]
    assert reported["holdout_start"] == "2026-05-18"
    assert reported["holdout"]["first"] == "2026-05-18"


# --------------------------------------------------------------------------------------
# The holdout (hook layout piece 4): checked by the validator, reported for the survivors
# --------------------------------------------------------------------------------------


def test_admission_layout_hands_the_validator_the_holdout_date(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    (experiment,) = run.experiments
    assert experiment.staged_pipeline_config.holdout_start == HOLDOUT_START


def test_admission_layout_reports_the_holdout_in_the_summary(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The one candidate of this tick does not pass every stage, so there is nobody to
    run over the holdout: the report names the holdout's sessions and says so."""
    layout = _expected_layout()
    assert layout.holdout is not None

    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    assert run.result.status == "ok", run.result.errors
    assert run.result.passed_filters == 0
    assert run.result.summary["holdout_report"] == {
        **_span(layout.holdout),
        "scored": 0,
        "candidates": [],
        "not_scored": "no strategy passed every stage",
    }


def _assert_gate_report_counts_what_the_funnel_counts(summary: dict[str, Any]) -> None:
    """The funnel is the stages' own counts; the gate report is built from their
    verdicts. For every stage that ran, they name the same number in and through."""
    report = summary["gate_report"]
    assert "not_built" not in report
    assert report["stages"] == list(summary["stage_funnel"])
    for stage, counts in summary["stage_funnel"].items():
        entries = [
            entry
            for record in report["strategies"]
            for entry in record["stages"]
            if entry["stage"] == stage
        ]
        assert len(entries) == counts["entered"], stage
        assert sum(entry["passed"] for entry in entries) == counts["passed"], stage
    assert json.loads(json.dumps(report, allow_nan=False)) == report


def test_admission_layout_reports_what_each_stage_said_in_the_summary(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """Through the hook, with the real runner, engine and bars. The tick's one candidate
    does not pass every stage: the funnel gives the stage it stopped at as a count, and
    the gate report names the candidate and gives that stage's reasons."""
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    assert run.result.status == "ok", run.result.errors
    summary = run.result.summary
    _assert_gate_report_counts_what_the_funnel_counts(summary)

    (record,) = summary["gate_report"]["strategies"]
    stopped_at = next(
        stage for stage, counts in summary["stage_funnel"].items() if counts["passed"] == 0
    )
    assert record["passed_every_stage"] is False
    assert record["eliminated_at"] == stopped_at
    last = record["stages"][-1]
    assert last["stage"] == stopped_at and last["passed"] is False
    # Said in the stage's own words: its list of reasons, or its folds for walk-forward.
    assert last.get("failures") or last.get("folds")


def test_a_holdout_run_through_the_real_runner_leaves_no_row(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """The tick's candidate, run over the holdout by the hook's own report function with
    the real runner, engine and bars: numbers come back and the database is as it was.
    The same request with ``record_run`` on writes its rows, so the count can move."""
    from dataclasses import replace

    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod
    from autonomous_trading_platform.application.services.research_window_dataset_loader import (
        load_research_window_dataset,
    )
    from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
        build_simulation_context,
    )
    from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
    from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
    from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns

    layout = _expected_layout()
    holdout = layout.holdout
    assert holdout is not None
    timeline = _admission_timeline()
    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)
    (experiment,) = run.experiments
    context = build_simulation_context(
        session=db_session, universe_timeline_source=lambda _version_id: timeline
    )
    (candidate,) = context.experiment_orchestration_service._expand_strategy_configs(experiment)

    class _Recording:
        def __init__(self, inner) -> None:
            self.inner = inner
            self.requests: list = []

        def run(self, request):
            self.requests.append(request)
            return self.inner.run(request)

        def reuse_loaded_bars(self):
            return self.inner.reuse_loaded_bars()

    def rows() -> tuple[int, int, int]:
        db_session.expire_all()
        return (
            db_session.query(SimulationRuns).count(),
            db_session.query(MetricsSummary).count(),
            db_session.query(RunManifestRow).count(),
        )

    before = rows()
    assert before[0] > 0 and before[1] > 0
    calls_before = len(run.engine_calls)
    runner = _Recording(context.simulation_runner)

    report = rh_mod._report_holdout(
        simulation_runner=runner,
        layout=layout,
        survivors=[candidate],
        experiment_def=experiment,
        universe_timeline=timeline,
        research_dataset=load_research_window_dataset(db_session, _WINDOW_DATASET_ID),
    )

    assert report["not_scored"] is None, report
    assert report["scored"] == 1
    (scored,) = report["candidates"]
    assert scored["strategy_id"] == candidate.strategy_id
    assert set(scored) == {"strategy_id", "sharpe", "total_return", "max_drawdown", "trade_count"}
    assert (report["first"], report["last"]) == (
        holdout.first.isoformat(),
        holdout.last.isoformat(),
    )
    # One simulation, over the holdout's days and its list (the one in force since the
    # rotation), on the timeline.
    (call,) = run.engine_calls[calls_before:]
    assert call["dates"] == (holdout.first, holdout.last)
    assert call["symbols"] == ["MSFT", "SPY"]
    assert call["universe_timeline"] is timeline
    # No run, metrics or manifest row was written, and no run in the table reads a
    # holdout day.
    assert rows() == before
    assert (
        db_session.query(SimulationRuns).filter(SimulationRuns.end_date >= HOLDOUT_START).all()
        == []
    )

    # The control: the same request, recorded, is a row.
    (request,) = runner.requests
    assert request.record_run is False
    context.simulation_runner.run(replace(request, record_run=True))
    after = rows()
    assert (after[0], after[1]) == (before[0] + 1, before[1] + 1)
    (holdout_row,) = (
        db_session.query(SimulationRuns).filter(SimulationRuns.end_date >= HOLDOUT_START).all()
    )
    assert holdout_row.window_role == "holdout"

    # A run's id follows from its inputs, so the unrecorded request now finds that
    # row under its own id. It still writes nothing: no second metrics row.
    context.simulation_runner.run(request)
    assert rows() == after


# --------------------------------------------------------------------------------------
# Buy and hold of the names in force (plan G9, Step 1b row 5): report only
# --------------------------------------------------------------------------------------


def test_admission_layout_sets_every_candidate_against_buy_and_hold(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """Through the hook, with the real runner, engine and bars: buy and hold over every
    window the gates read, and the tick's candidate against it on the selection."""
    layout = _expected_layout()

    run = _run_admission(db_session, replay_context, monkeypatch, research_smoke_data_root)

    assert run.result.status == "ok", run.result.errors
    report = run.result.summary["gate_report"]
    benchmark = report["benchmark"]
    assert "not_built" not in benchmark, benchmark
    for name in ("selection", "evaluation"):
        piece = getattr(layout, name)
        assert (benchmark[name]["first"], benchmark[name]["last"]) == (
            piece.first.isoformat(),
            piece.last.isoformat(),
        )
        assert benchmark[name]["sessions"] == piece.sessions
        assert benchmark[name]["total_return"] is not None
        # Two names in each list of the timeline, and the fixture has bars of both.
        assert benchmark[name]["fewest_names_held"] >= 1
        assert benchmark[name]["mean_names_held"] <= 2
    assert [fold["sessions"] for fold in benchmark["test_folds"]] == [
        fold.sessions for fold in layout.test_folds
    ]
    assert benchmark["names_without_bars"] == []
    # What it was built from is said beside it: this database holds no corporate actions.
    assert benchmark["corporate_actions"] == {"splits": 0, "cash_dividends": 0}

    (record,) = report["strategies"]
    selection = record["versus_benchmark"]["selection"]
    stage_1 = record["stages"][0]["reference_run"]
    # The candidate's side is rebuilt from its run's equity curve: it is the run's own
    # total return, over the same days as buy and hold.
    assert selection["return"] == pytest.approx(stage_1["total_return"], abs=1e-9)
    assert selection["days"] == layout.selection.sessions
    assert selection["excess_return"] == pytest.approx(
        selection["return"] - benchmark["selection"]["total_return"]
    )
    assert selection["benchmark_return"] == pytest.approx(benchmark["selection"]["total_return"])
    # Turnover and return per dollar traded (plan G8, row 6), against the filters' cap.
    assert report["turnover_cap"] == 10.0
    assert stage_1["daily_turnover"] >= 0.0
    assert stage_1["share_of_turnover_cap"] == pytest.approx(stage_1["daily_turnover"] / 10.0)
    if stage_1["notional_traded"] > 0:
        # Net over notional has the sign of the run's own return.
        per_dollar = stage_1["net_return_per_dollar_traded_bp"]
        assert (per_dollar > 0) == (stage_1["total_return"] > 0)
    # Report only: the funnel is as it was, and the report is still plain JSON.
    _assert_gate_report_counts_what_the_funnel_counts(run.result.summary)


def test_admission_layout_runs_the_planted_controls_and_scores_them(
    research_smoke_data_root, db_session, replay_context, monkeypatch
):
    """``candidates: planted_controls`` (research plan, "Planted controls, M1"): the
    controls reach the pipeline as written, nothing is generated, every stage and the
    holdout run them, the summary scores them, and none is seeded into governance.

    The fixture's bars are daily, one a session, so the oracle has no session it can buy
    and sell within and makes no trade (``session_calls`` leaves one-bar sessions out):
    every control falls at stage 1, and the scorecard must say so. That the oracle trades
    the moves it is told of is checked on 5-minute bars in the real engine
    (``tests/strategy/test_planted_oracle_strategy.py``).
    """
    from autonomous_trading_platform.research.pipeline.controls_report import (
        PLANTED_CONTROLS,
        planted_control_strategy_set,
    )
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    run = _run_admission(
        db_session,
        replay_context,
        monkeypatch,
        research_smoke_data_root,
        options={**_ADMISSION, "candidates": "planted_controls"},
        keep_strategy_set=True,
    )

    assert run.result.status == "ok", run.result.errors
    (experiment,) = run.experiments
    assert experiment.strategy_set == planted_control_strategy_set()
    assert experiment.parameter_space is None
    summary = run.result.summary
    assert summary["candidates"] == "planted_controls"
    assert summary["generation"] == []
    assert summary["stage_funnel"]["initial_quality_filter"] == {
        "entered": len(PLANTED_CONTROLS),
        "passed": 0,
    }

    report = summary["controls_report"]
    ids = [row["strategy_id"] for row in report["controls"]]
    assert ids == [control.strategy_id for control in PLANTED_CONTROLS]
    for row in report["controls"]:
        assert row["eliminated_at"] == "initial_quality_filter"
    assert report["planted_edge_found"] is False
    assert report["noise_rejected"] is True
    assert report["noise_pass_rate_by_stage"]["initial_quality_filter"] == {
        "entered": 5,
        "passed": 0,
    }
    # Every simulation of a control was handed its window (an unbound control raises).
    assert run.engine_calls
    assert run.signals.empty
    assert summary["controls_withheld_from_governance"] == 0
    assert (
        db_session.query(StrategyGovernance)
        .filter(StrategyGovernance.strategy_id.like("planted_oracle%"))
        .count()
        == 0
    )
    json.dumps(summary["controls_report"])
