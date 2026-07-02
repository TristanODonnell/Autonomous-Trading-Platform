"""Isolated monthly research runner.

Runs the exact same run_scheduled_research_at_timestamp that fires during
the two_year_full.yaml platform backtest -- same 13 symbols, same 3-stage
pipeline (quality filter / walk-forward / Monte Carlo), same governance seeding
-- without running 39,000+ trading cycle ticks first.

Synthetic bars are seeded into a temp Parquet directory so no real ingestion
or Postgres is required.  Everything runs against SQLite in-memory.

Usage
-----
    # Default: February 2023, 2 candidates per strategy type (10 simulations)
    python scripts/run_research_month.py

    # Pick a different month
    python scripts/run_research_month.py --month 2023-03

    # Match production candidate counts (6/6/6/8/10 = 36 simulations)
    python scripts/run_research_month.py --month 2023-06 --full

    # 5-min bars instead of daily (higher fidelity, ~78x more bars, ~78x slower)
    python scripts/run_research_month.py --intraday

    # Override lookback window (default: 60 calendar days before month end)
    python scripts/run_research_month.py --lookback-days 90
"""

from __future__ import annotations

import argparse
import calendar
import os
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# ── env setup BEFORE any platform imports ─────────────────────────────────────
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("NO_LIVE_TRADING", "true")
os.environ.setdefault("JWT_SECRET", "dev-secret-not-for-production")

# ── Now safe to import platform code ──────────────────────────────────────────
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis  # noqa: E402
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,  # noqa: E402
)
from autonomous_trading_platform.db import get_engine  # noqa: E402
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET  # noqa: E402
from autonomous_trading_platform.storage.parquet.metadata import (  # noqa: E402
    attach_metadata,
    build_metadata,
)
from autonomous_trading_platform.storage.parquet.paths import partition_file_path  # noqa: E402
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA  # noqa: E402
from autonomous_trading_platform.storage.sor.models.dataset_versions import (
    DatasetVersions,  # noqa: E402
)

# ── Two-year backtest universe (from two_year_full.yaml) ──────────────────────
_FIXTURE_SYMBOLS = [
    "SPY",
    "QQQ",
    "IWM",
    "GLD",
    "TLT",  # broad market / macro
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",  # large-cap tech
    "JPM",
    "JNJ",
    "XOM",
    "WMT",  # large-cap non-tech
]

_DATASET_VERSION_ID = "research_month_script_v1"


# ── Bar generation helpers ─────────────────────────────────────────────────────


def _trading_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _make_daily_bars(symbol: str, trading_days: list[date]) -> list[dict]:
    import math
    import random as _rnd

    now = datetime(2023, 1, 1, tzinfo=UTC)
    base_price = {
        "SPY": 380.0,
        "QQQ": 280.0,
        "IWM": 175.0,
        "GLD": 170.0,
        "TLT": 100.0,
        "AAPL": 130.0,
        "MSFT": 240.0,
        "NVDA": 145.0,
        "GOOGL": 95.0,
        "JPM": 130.0,
        "JNJ": 165.0,
        "XOM": 110.0,
        "WMT": 140.0,
    }.get(symbol, 100.0)
    # Each symbol gets a deterministic but distinct seed so they don't all move together.
    rng = _rnd.Random(hash(symbol) & 0xFFFFFFFF)

    bars = []
    price = base_price
    for i, d in enumerate(trading_days):
        ts = datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)
        # Random walk with mild trend + sine cycle to drive signal changes.
        # Volatility ~1.5%/day makes strategies cross their thresholds regularly.
        daily_return = rng.gauss(0.0003, 0.015) + 0.004 * math.sin(2 * math.pi * i / 10)
        price = max(price * (1 + daily_return), base_price * 0.5)
        close = price
        bar_id = f"{symbol}_{d.isoformat()}_1d"
        bars.append(
            {
                "bar_id": bar_id,
                "timestamp": ts,
                "end_timestamp": ts + timedelta(hours=6, minutes=30),
                "interval": BarInterval.ONE_DAY.value,
                "symbol": symbol,
                "open": close * (1 + rng.gauss(0, 0.003)),
                "high": close * (1 + abs(rng.gauss(0, 0.008))),
                "low": close * (1 - abs(rng.gauss(0, 0.008))),
                "close": close,
                "volume": int(5_000_000 * (1 + rng.gauss(0, 0.3))),
                "vwap": close,
                "trade_count": 80_000,
                "price_basis": PriceBasis.RAW.value,
                "adjustment_factor": 1.0,
                "source": "research_month_script",
                "ingested_at": now,
                "quality_flags": None,
                "date": d,
                "year": f"{d.year:04d}",
                "month": f"{d.month:02d}",
            }
        )
    return bars


def _make_fivemin_bars(symbol: str, trading_days: list[date]) -> list[dict]:
    import random as _rnd

    now = datetime(2023, 1, 1, tzinfo=UTC)
    base_price = {
        "SPY": 380.0,
        "QQQ": 280.0,
        "IWM": 175.0,
        "GLD": 170.0,
        "TLT": 100.0,
        "AAPL": 130.0,
        "MSFT": 240.0,
        "NVDA": 145.0,
        "GOOGL": 95.0,
        "JPM": 130.0,
        "JNJ": 165.0,
        "XOM": 110.0,
        "WMT": 140.0,
    }.get(symbol, 100.0)
    rng = _rnd.Random(hash(symbol) & 0xFFFFFFFF)

    bars = []
    price = base_price
    for d in trading_days:
        open_time = datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)
        for slot in range(78):
            ts = open_time + timedelta(minutes=5 * slot)
            price = max(price * (1 + rng.gauss(0.00003, 0.002)), base_price * 0.5)
            close = price
            bar_id = f"{symbol}_{d.isoformat()}_{slot:03d}_5m"
            bars.append(
                {
                    "bar_id": bar_id,
                    "timestamp": ts,
                    "end_timestamp": ts + timedelta(minutes=5),
                    "interval": BarInterval.FIVE_MIN.value,
                    "symbol": symbol,
                    "open": close * (1 + rng.gauss(0, 0.001)),
                    "high": close * (1 + abs(rng.gauss(0, 0.002))),
                    "low": close * (1 - abs(rng.gauss(0, 0.002))),
                    "close": close,
                    "volume": int(50_000 * (1 + rng.gauss(0, 0.3))),
                    "vwap": close,
                    "trade_count": 800,
                    "price_basis": PriceBasis.RAW.value,
                    "adjustment_factor": 1.0,
                    "source": "research_month_script",
                    "ingested_at": now,
                    "quality_flags": None,
                    "date": d,
                    "year": f"{d.year:04d}",
                    "month": f"{d.month:02d}",
                }
            )
    return bars


def _write_symbol_parquet(data_root: Path, symbol: str, bars: list[dict]) -> None:
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
            ingestion_timestamp=datetime.now(UTC).isoformat(),
            checksum="research_month_script_checksum",
        )
        schema_with_meta = attach_metadata(table.schema, metadata_dict)
        table = table.cast(schema_with_meta)

        pq.write_table(table, file_path)


def _patch_parquet_paths(data_root: Path) -> None:
    """Monkey-patch all platform parquet readers to use data_root."""
    import autonomous_trading_platform.research.simulation.contexts.build_simulation_context as ctx_mod

    _orig_build = ctx_mod.build_simulation_context

    def _patched_build(*, session, universe_size=None, lookback_bars=50):
        ctx = _orig_build(session=session, universe_size=universe_size, lookback_bars=lookback_bars)
        ctx.bar_reader.base_path = data_root
        ctx.dataset_resolver.base_path = data_root
        ctx.window_loader.bar_reader.base_path = data_root
        if hasattr(ctx.window_loader, "feature_reader") and ctx.window_loader.feature_reader:
            ctx.window_loader.feature_reader.base_path = data_root
        ctx.simulation_runner.dataset_resolver.base_path = data_root
        return ctx

    ctx_mod.build_simulation_context = _patched_build


def _register_dataset(session: Session, start: date, end: date, interval: BarInterval) -> None:
    row = DatasetVersions(
        dataset_version_id=_DATASET_VERSION_ID,
        dataset_name="raw_bars",
        created_at=datetime.now(UTC),
        source="research_month_script",
        price_basis=PriceBasis.RAW,
        interval=interval,
        schema_version=RAW_BARS_DATASET.schema_version,
        symbol_coverage=len(_FIXTURE_SYMBOLS),
        date_coverage_start=start,
        date_coverage_end=end,
        validation_status="validated",
        checksum="research_month_script_checksum",
    )
    session.add(row)
    session.flush()


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--month",
        default="2023-02",
        metavar="YYYY-MM",
        help="Month to run research for (default: 2023-02, the first full month in two_year_full.yaml)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        metavar="N",
        help="Calendar days of bar data to seed before month-end (default: 60)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use production candidate counts (6/6/6/8/10 = 36 simulations). "
        "Default is 2/2/2/2/2 = 10 for faster iteration.",
    )
    parser.add_argument(
        "--intraday",
        action="store_true",
        help="Seed 5-min bars (78/day) instead of daily bars. Slower but matches production data format.",
    )
    parser.add_argument(
        "--relax-filters",
        action="store_true",
        help="Use near-zero filter thresholds so every candidate passes every stage. "
        "Use this to verify Stage 2 and Stage 3 execute mechanically without needing synthetic alpha.",
    )
    args = parser.parse_args()

    # Parse month
    try:
        year_str, month_str = args.month.split("-")
        year, month = int(year_str), int(month_str)
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, AttributeError):
        print(f"ERROR: --month must be YYYY-MM, got {args.month!r}", file=sys.stderr)
        return 1

    tick_date = _month_end(year, month)
    tick_ts = datetime(tick_date.year, tick_date.month, tick_date.day, 21, 0, tzinfo=UTC)
    bar_start = tick_date - timedelta(days=args.lookback_days)
    trading_days = _trading_days(bar_start, tick_date)
    interval = BarInterval.FIVE_MIN if args.intraday else BarInterval.ONE_DAY

    n_samples = (
        {
            "momentum": 6,
            "mean_reversion": 6,
            "moving_average_crossover": 6,
            "factor_based": 8,
            "composite_rule": 10,
        }
        if args.full
        else {
            "momentum": 2,
            "mean_reversion": 2,
            "moving_average_crossover": 2,
            "factor_based": 2,
            "composite_rule": 2,
        }
    )
    total_candidates = sum(n_samples.values())

    print(f"\n{'=' * 60}")
    print("  Monthly Research Runner")
    print(f"{'=' * 60}")
    print(f"  Month:          {tick_date.strftime('%B %Y')} (tick {tick_ts.date()})")
    print(f"  Universe:       {len(_FIXTURE_SYMBOLS)} symbols ({', '.join(_FIXTURE_SYMBOLS)})")
    print(f"  Bar window:     {bar_start} to {tick_date} ({len(trading_days)} trading days)")
    print(f"  Bar cadence:    {'5-min (78/day)' if args.intraday else 'daily (1/day)'}")
    print(f"  Candidates:     {total_candidates} total ({n_samples})")
    print(
        f"  Mode:           {'production' if args.full else 'fast-dev (use --full for production counts)'}"
    )
    print(f"{'=' * 60}\n")

    with tempfile.TemporaryDirectory(prefix="research_month_") as tmpdir:
        data_root = Path(tmpdir)

        # 1 — Seed synthetic bars
        bar_fn = _make_fivemin_bars if args.intraday else _make_daily_bars
        bars_per_symbol = len(trading_days) * (78 if args.intraday else 1)
        print(
            f"[1/4] Seeding bars for {len(_FIXTURE_SYMBOLS)} symbols "
            f"({bars_per_symbol:,} bars/symbol)...",
            end=" ",
            flush=True,
        )
        t0 = time.perf_counter()
        for sym in _FIXTURE_SYMBOLS:
            bars = bar_fn(sym, trading_days)
            _write_symbol_parquet(data_root, sym, bars)
        print(f"done ({time.perf_counter() - t0:.1f}s)")

        # 2 — Patch parquet paths before any platform DB/context is created
        _patch_parquet_paths(data_root)

        # 3 — Set up SQLite in-memory DB
        print("[2/4] Setting up SQLite in-memory DB...", end=" ", flush=True)
        t0 = time.perf_counter()
        engine = get_engine()
        from sqlalchemy.orm import sessionmaker

        Session_ = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        session = Session_()
        _register_dataset(session, bar_start, tick_date, interval)
        print(f"done ({time.perf_counter() - t0:.1f}s)")

        # 4 — Build PlatformReplayContext (same symbols as two_year_full.yaml)
        replay_context = PlatformReplayContext.create(
            symbols=_FIXTURE_SYMBOLS,
            actor="research_month_script",
            timestamp=tick_ts,
        )

        # 5 — Override candidate counts (patch _build_replay_experiment_definition)
        import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh_mod

        _orig_build_exp = rh_mod._build_replay_experiment_definition

        _n_samples = n_samples  # capture for closure
        _bar_start = bar_start  # capture full seeded window for closure
        _relax = args.relax_filters

        def _controlled_experiment(
            *,
            session,
            timestamp,
            replay_context,
            simulation_runner,
            dataset_version_id_override=None,
        ):
            from dataclasses import replace

            from autonomous_trading_platform.research.experiments.filtering.config import (
                FilterConfig,
                ScoringWeights,
            )
            from autonomous_trading_platform.research.pipeline.pipeline_runner import (
                StagedPipelineConfig,
            )
            from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
                MonteCarloStage,
                MonteCarloStageConfig,
            )
            from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
                SimulationStage,
                SimulationStageConfig,
            )
            from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
                WalkForwardStage,
                WalkForwardStageConfig,
            )

            exp = _orig_build_exp(
                session=session,
                timestamp=timestamp,
                replay_context=replay_context,
                simulation_runner=simulation_runner,
                dataset_version_id_override=dataset_version_id_override,
            )
            if exp is None:
                return None

            new_strategy_set = [
                {"type": t, "method": "random", "options": {"n_samples": _n_samples[t]}}
                for t in (
                    "momentum",
                    "mean_reversion",
                    "moving_average_crossover",
                    "factor_based",
                    "composite_rule",
                )
            ]

            # Expand start_date to the full seeded range — the hook caps at 90 days,
            # which is enough for 5-min bars but thin for daily bars in the script.
            exp = replace(exp, strategy_set=new_strategy_set, start_date=_bar_start)

            if not _relax:
                return exp

            # --relax-filters: rebuild all 3 stages with near-zero thresholds so every
            # candidate passes regardless of metrics.  Verifies the mechanical pipeline
            # (fold execution, handoff, Monte Carlo shuffle) without needing synthetic alpha.
            open_filter = FilterConfig(
                min_sharpe=-100.0,
                max_drawdown=-1.0,
                min_trades=0,
                min_consistency_score=-1.0,
                min_profit_factor=0.0,  # 0.0 disables the profit_factor check
                min_total_return=-100.0,
            )
            weights = ScoringWeights()
            syms = exp.symbols
            sd, ed = exp.start_date, exp.end_date

            stage1 = SimulationStage(
                stage_config=SimulationStageConfig(
                    name="initial_quality_filter",
                    start_date=sd,
                    end_date=ed,
                    symbols=syms,
                    filter_config=open_filter,
                    scoring_weights=weights,
                    max_workers=1,
                ),
                simulation_runner=simulation_runner,
            )
            stage2 = WalkForwardStage(
                stage_config=WalkForwardStageConfig(
                    name="walk_forward_robustness",
                    symbols=syms,
                    start_date=sd,
                    end_date=ed,
                    train_days=30,
                    test_days=20,
                    step_days=10,
                    train_filter_config=open_filter,
                    train_scoring_weights=weights,
                    test_filter_config=open_filter,
                    test_scoring_weights=weights,
                    require_all_folds=False,
                    min_folds_passed=1,
                    max_workers=1,
                ),
                simulation_runner=simulation_runner,
            )
            stage3 = MonteCarloStage(
                stage_config=MonteCarloStageConfig(
                    name="monte_carlo_structural_robustness",
                    symbols=syms,
                    start_date=sd,
                    end_date=ed,
                    n_runs=3,
                    min_pass_rate=0.01,
                    filter_config=open_filter,
                    scoring_weights=weights,
                    max_workers=1,
                ),
                simulation_runner=simulation_runner,
            )
            return replace(
                exp, staged_pipeline_config=StagedPipelineConfig(stages=[stage1, stage2, stage3])
            )

        rh_mod._build_replay_experiment_definition = _controlled_experiment

        # 6 — Run the research tick
        from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
            run_scheduled_research_at_timestamp,
        )

        print(f"[3/4] Running research pipeline for {tick_date.strftime('%B %Y')}...")
        t_research = time.perf_counter()

        result = run_scheduled_research_at_timestamp(
            session=session,
            timestamp=tick_ts,
            replay_context=replay_context,
            dataset_version_id=_DATASET_VERSION_ID,
        )

        elapsed = time.perf_counter() - t_research
        session.close()

        # 7 — Report
        print(f"\n[4/4] Results ({elapsed:.1f}s)\n")
        print(f"{'=' * 60}")
        print(f"  Status:         {result.status}")
        if result.errors:
            print(f"  Errors:         {result.errors}")
        if result.warnings:
            print(f"  Warnings:       {result.warnings}")
        if result.summary:
            for k, v in result.summary.items():
                if k != "timestamp":
                    print(f"  {k + ':':<26} {v}")
        print(f"{'=' * 60}\n")

        if result.status == "failed":
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
