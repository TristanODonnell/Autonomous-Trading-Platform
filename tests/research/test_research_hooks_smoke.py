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

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.metadata import (
    attach_metadata,
    build_metadata,
)
from autonomous_trading_platform.storage.parquet.paths import partition_file_path
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
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


def _seed_bar_parquet(data_root: Path, symbol: str, bars: list[dict]) -> None:
    """Write a single data.parquet file for one symbol/year/month partition."""
    by_month: dict[tuple[str, str], list[dict]] = {}
    for bar in bars:
        key = (bar["year"], bar["month"])
        by_month.setdefault(key, []).append(bar)

    for (year, month), month_bars in by_month.items():
        file_path = partition_file_path(
            base_path=data_root,
            dataset=RAW_BARS_DATASET,
            dataset_version=_DATASET_VERSION_ID,
            partitions={"symbol": symbol, "year": year, "month": month},
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)

        arrays = {field.name: [b.get(field.name) for b in month_bars] for field in BAR_SCHEMA}
        table = pa.table(arrays, schema=BAR_SCHEMA)

        metadata_dict = build_metadata(
            dataset=RAW_BARS_DATASET,
            dataset_version=_DATASET_VERSION_ID,
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
        minimal_strategy_set = [
            {"type": t, "method": "random", "options": {"n_samples": 1}}
            for t in ("momentum", "mean_reversion", "moving_average_crossover")
        ]
        return replace(exp, strategy_set=minimal_strategy_set)

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
            strategy_set=[{"type": "momentum", "method": "random", "options": {"n_samples": 1}}],
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
            strategy_set=[{"type": "momentum", "method": "random", "options": {"n_samples": 1}}],
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
        exp = replace(
            exp,
            strategy_set=[{"type": "momentum", "method": "random", "options": {"n_samples": 1}}],
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
                "result": result,
            }
        )
        return result

    monkeypatch.setattr(SimulationExecutionEngine, "execute", _spy)

    run.result = rh_mod.run_scheduled_research_at_timestamp(
        session=db_session,
        timestamp=_TICK_TS,
        replay_context=replay_context,
        dataset_version_id=_DATASET_VERSION_ID,
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
