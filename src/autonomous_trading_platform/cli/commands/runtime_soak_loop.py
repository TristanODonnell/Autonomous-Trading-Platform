from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autonomous_trading_platform.cli.commands.research import _load_experiment_from_yaml
from autonomous_trading_platform.cli.formatters import print_error, print_header
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
)
from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
    build_simulation_context,
)
from autonomous_trading_platform.runtime.clock import (
    MarketPhase,
    RealMarketCalendar,
    RealTradingClock,
)
from autonomous_trading_platform.runtime.interruptible_sleep import InterruptibleSleeper
from autonomous_trading_platform.runtime.services.orphan_job_recovery_service import (
    OrphanJobRecoveryService,
)
from autonomous_trading_platform.scheduler.orchestration.historical_research_golden_path_orchestrator import (
    HistoricalResearchGoldenPathOrchestrator,
)
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.scheduler.registry.no_overlap_lock import InMemoryNoOverlapLock
from autonomous_trading_platform.scheduler.registry.scheduler_registry import SCHEDULER_REGISTRY

_ET = ZoneInfo("America/New_York")
_INTRADAY_LOCK_KEY = SCHEDULER_REGISTRY["market_ingestion_cycle"].lock_key
_EOD_LOCK_KEY = SCHEDULER_REGISTRY["corporate_action_ingestion_cycle"].lock_key
_INTRADAY_INTERVAL_SECONDS = 300


def _parse_symbols(raw: str) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")
    return symbols


def _now_et() -> datetime:
    return datetime.now(tz=_ET)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _ts() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%S")


def _fmt_duration(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _load_experiment_plan(path: Path, simulation_context: object) -> ExperimentDefinition:
    return _load_experiment_from_yaml(str(path), simulation_context)


class _PaperTradingSoakRunner:
    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._lock = InMemoryNoOverlapLock()
        self._clock = RealTradingClock()
        self._calendar = RealMarketCalendar()
        self._sleeper = InterruptibleSleeper()
        self._intraday_cycles = 0
        self._eod_cycles = 0
        self._locks_acquired = 0
        self._locks_skipped = 0
        self._eod_done_for: date | None = None

    def _run_intraday_tick(self) -> None:
        tick_num = self._intraday_cycles + 1
        print(f"[{_ts()}] Running intraday tick #{tick_num}")
        acquired = self._lock.acquire(_INTRADAY_LOCK_KEY)
        if not acquired:
            self._locks_skipped += 1
            print("  ⚠ Lock acquisition failed (cycle still running from previous tick)")
            print("  Skipped this cycle")
            return
        self._locks_acquired += 1
        session = get_session()
        try:
            print(f"  Lock acquired: {_INTRADAY_LOCK_KEY}")
            print("  Steps: market_ingestion → features → trading")
            orchestrator = PaperTradingGoldenPathOrchestrator(session)
            result = orchestrator.run_intraday_tick(now_utc=self._clock.now())
            print("  Lock released")
            print(f"  ✓ Completed (correlation_id: {result.correlation_id})")
            self._intraday_cycles += 1
        except Exception as exc:
            print(f"  ⚠ Error during intraday tick: {exc}")
        finally:
            session.close()
            self._lock.release(_INTRADAY_LOCK_KEY)

    def _run_eod_maintenance(self) -> None:
        print(f"[{_ts()}] Running EOD maintenance")
        acquired = self._lock.acquire(_EOD_LOCK_KEY)
        if not acquired:
            self._locks_skipped += 1
            print("  ⚠ Lock acquisition failed (EOD cycle already running)")
            return
        self._locks_acquired += 1
        session = get_session()
        try:
            print(f"  Lock acquired: {_EOD_LOCK_KEY}")
            print("  Steps: corporate_actions → features (adjusted bars)")
            orchestrator = PaperTradingGoldenPathOrchestrator(session)
            result = orchestrator.run_eod_maintenance(now_utc=self._clock.now())
            self._eod_done_for = self._clock.now().astimezone(_ET).date()
            print("  Lock released")
            print(f"  ✓ Completed (correlation_id: {result.correlation_id})")
            self._eod_cycles += 1
        except Exception as exc:
            print(f"  ⚠ Error during EOD maintenance: {exc}")
        finally:
            session.close()
            self._lock.release(_EOD_LOCK_KEY)

    def _print_stats(self) -> None:
        total = self._locks_acquired + self._locks_skipped
        print(
            f"[SoakTestRunner] Completed {self._intraday_cycles} intraday cycles, "
            f"{self._eod_cycles} EOD runs"
        )
        print(
            f"[SoakTestRunner] Lock stats: {self._locks_acquired} acquired, "
            f"{self._locks_skipped} skipped / {total} total"
        )

    def _rescue_orphan_jobs(self) -> None:
        cutoff = self._clock.now() - timedelta(minutes=30)
        session = get_session()
        try:
            rescued = OrphanJobRecoveryService(session).rescue_orphan_running_jobs(cutoff=cutoff)
            session.commit()
            if rescued:
                print(
                    f"[SoakTestRunner] Rescued {len(rescued)} orphan RUNNING job(s) from prior run: "
                    + ", ".join(r.job_name for r in rescued)
                )
        finally:
            session.close()

    def run(self) -> int:
        self._sleeper.install_signal_handlers(label="SoakTestRunner")
        mode_label = {
            "fast": "fast mode",
            "realistic": "realistic mode",
            "single": "single tick",
        }.get(self._mode, self._mode)
        print_header(f"Paper Trading Soak Loop — {mode_label}")
        print("[SoakTestRunner] WARNING: This makes real API calls to Alpaca paper trading")
        print("[SoakTestRunner] Lock manager: InMemoryNoOverlapLock")
        self._rescue_orphan_jobs()

        if self._mode == "single":
            return self._run_single()
        interval = 0 if self._mode == "fast" else _INTRADAY_INTERVAL_SECONDS
        return self._run_loop(intraday_interval=interval)

    def _run_single(self) -> int:
        now_utc = self._clock.now()
        phase = self._calendar.market_phase(now_utc)
        print(f"[SoakTestRunner] Market phase: {phase.value}")
        if phase != MarketPhase.MARKET_HOURS:
            print("[SoakTestRunner] Market is not open — single mode requires MARKET_HOURS")
            return 1
        self._run_intraday_tick()
        self._print_stats()
        return 0

    def _run_loop(self, intraday_interval: int) -> int:
        last_phase: MarketPhase | None = None
        waiting_for: str | None = None

        while not self._sleeper.is_shutdown:
            now_utc = self._clock.now()
            phase = self._calendar.market_phase(now_utc)

            if phase != last_phase:
                if last_phase == MarketPhase.MARKET_HOURS and phase == MarketPhase.POST_MARKET:
                    print(f"[{_ts()}] Market phase: POST_MARKET")
                    print(f"[{_ts()}] Market closed — stopping intraday ticks")
                else:
                    print(f"[{_ts()}] Market phase: {phase.value}")
                last_phase = phase
                waiting_for = None

            if phase == MarketPhase.MARKET_HOURS:
                self._run_intraday_tick()
                if not self._sleeper.is_shutdown and intraday_interval > 0:
                    today_et = now_utc.astimezone(_ET).date()
                    close_utc = self._calendar.market_close(today_et)
                    secs_to_close = max(0, int((close_utc - self._clock.now()).total_seconds()))
                    sleep_secs = min(intraday_interval, secs_to_close)
                    if sleep_secs > 0:
                        next_tick = (_now_et() + timedelta(seconds=sleep_secs)).strftime("%H:%M:%S")
                        print(
                            f"[SoakTestRunner] Sleeping {sleep_secs}s until next tick "
                            f"({next_tick} ET)..."
                        )
                        self._sleeper.sleep(sleep_secs)
            elif self._calendar.is_eod_eligible(now_utc, last_eod_date=self._eod_done_for):
                waiting_for = None
                self._run_eod_maintenance()
            else:
                today_et = now_utc.astimezone(_ET).date()
                eod_window_utc = self._calendar.eod_window_open(today_et)
                if (
                    phase == MarketPhase.POST_MARKET
                    and now_utc < eod_window_utc
                    and self._eod_done_for != today_et
                ):
                    secs = max(0, int((eod_window_utc - now_utc).total_seconds()))
                    if waiting_for != "eod":
                        print(
                            f"[SoakTestRunner] EOD maintenance at 18:00 ET "
                            f"(in {_fmt_duration(secs)}), sleeping..."
                        )
                        waiting_for = "eod"
                    self._sleeper.sleep(min(secs, 60))
                else:
                    secs = self._calendar.seconds_until_next_session_open(now_utc)
                    if waiting_for != "market_open":
                        print(
                            f"[SoakTestRunner] Next market open in "
                            f"{_fmt_duration(secs)}, sleeping..."
                        )
                        waiting_for = "market_open"
                    self._sleeper.sleep(min(secs, 300))

        self._print_stats()
        return 0


class _ResearchSoakRunner:
    def __init__(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        loop: bool,
        experiment_plan: ExperimentDefinition | None,
    ) -> None:
        self._symbols = symbols
        self._start = start
        self._end = end
        self._loop = loop
        self._experiment_plan = experiment_plan
        self._sleeper = InterruptibleSleeper()
        self._cycles = 0

    def run(self) -> int:
        self._sleeper.install_signal_handlers(label="SoakTestRunner")
        mode_label = "loop mode" if self._loop else "one-shot"
        print_header(f"Historical Research Soak Loop — {mode_label}")
        print(f"[SoakTestRunner] Symbols: {', '.join(self._symbols)}")
        print(f"[SoakTestRunner] Period: {self._start.date()} to {self._end.date()}")
        if self._loop:
            print(
                "[SoakTestRunner] WARNING: This will repeatedly backfill the same period "
                "(soak test)"
            )

        while not self._sleeper.is_shutdown:
            cycle_num = self._cycles + 1
            label = f"pipeline #{cycle_num}" if self._loop else "pipeline"
            print(f"[{_ts()}] Running historical research {label}")
            session = get_session()
            try:
                orchestrator = HistoricalResearchGoldenPathOrchestrator(session)
                result = orchestrator.run(
                    symbols=self._symbols,
                    start=self._start,
                    end=self._end,
                    now_utc=_now_utc(),
                    experiment_plan=self._experiment_plan,
                )
                print(f"  ✓ Completed (correlation_id: {result.correlation_id})")
                self._cycles += 1
            except Exception as exc:
                print(f"  ⚠ Error: {exc}")
            finally:
                session.close()

            if not self._loop:
                break

        if self._loop:
            print(f"[SoakTestRunner] Completed {self._cycles} research pipeline cycles")
        else:
            print("[SoakTestRunner] Historical research complete")
        return 0


def register_soak_loop_commands(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Backtest replay: walk historical bars and write fills/snapshots to DB",
    )
    backtest_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. SPY,QQQ",
    )
    backtest_parser.add_argument(
        "--start",
        required=True,
        help="Start date (ISO8601), e.g. 2024-01-01",
    )
    backtest_parser.add_argument(
        "--end",
        required=True,
        help="End date (ISO8601), e.g. 2024-12-31",
    )
    backtest_parser.add_argument(
        "--initial-capital",
        dest="initial_capital",
        type=float,
        default=100_000.0,
        help="Starting portfolio capital in USD (default: 100000)",
    )
    backtest_parser.add_argument(
        "--strategy-id",
        dest="strategy_id",
        default="baseline_strategy",
        help="Strategy ID to tag fills and positions with",
    )
    backtest_parser.set_defaults(func=handle_soak_loop_backtest)

    paper_parser = subparsers.add_parser(
        "paper",
        help="Paper trading soak loop (market-hours aware, real Alpaca API)",
    )
    paper_parser.add_argument(
        "--mode",
        choices=["fast", "realistic", "single"],
        default="realistic",
        help="fast=back-to-back ticks, realistic=5min intervals, single=one tick then exit",
    )
    paper_parser.set_defaults(func=handle_soak_loop_paper)

    research_parser = subparsers.add_parser(
        "research",
        help="Historical research soak loop (no market-hours restriction)",
    )
    research_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of symbols, e.g. AAPL,MSFT,GOOGL",
    )
    research_parser.add_argument(
        "--start",
        required=True,
        help="Backfill start date/datetime (ISO8601)",
    )
    research_parser.add_argument(
        "--end",
        required=True,
        help="Backfill end date/datetime (ISO8601)",
    )
    research_parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat the research pipeline indefinitely for soak testing",
    )
    research_parser.add_argument(
        "--experiment-plan",
        dest="experiment_plan",
        metavar="FILE",
        help="Path to experiment plan YAML file (optional, same format as atp research run-experiment --config)",
    )
    research_parser.set_defaults(func=handle_soak_loop_research)


def handle_soak_loop_backtest(args: argparse.Namespace) -> int:
    from decimal import Decimal

    from autonomous_trading_platform.scheduler.backtest.backtest_config import BacktestConfig
    from autonomous_trading_platform.scheduler.backtest.backtest_trading_cycle_orchestrator import (
        BacktestTradingCycleOrchestrator,
    )

    symbols = _parse_symbols(args.symbols)
    start = parse_datetime(args.start).date()
    end = parse_datetime(args.end).date()

    cfg = BacktestConfig(
        symbols=symbols,
        start_date=start,
        end_date=end,
        initial_capital=Decimal(str(args.initial_capital)),
        strategy_id=args.strategy_id,
    )

    print_header("Backtest Trading Cycle")
    print(f"[Backtest] Symbols:         {', '.join(symbols)}")
    print(f"[Backtest] Period:          {start} to {end}")
    print(f"[Backtest] Initial capital: ${float(cfg.initial_capital):,.2f}")
    print(f"[Backtest] Strategy ID:     {cfg.strategy_id}")
    print(
        "[Backtest] Pipeline:        backfill → features → evaluation → simulated fills → snapshots"
    )
    print()

    try:
        orchestrator = BacktestTradingCycleOrchestrator(config=cfg)
        result = orchestrator.run(progress=True)
    except Exception as exc:
        print_error(f"Backtest failed: {exc}")
        return 1

    print()
    print(f"[Backtest] Run ID:          {result.run_id}")
    print(f"[Backtest] Bars processed:  {result.bars_processed:,}")
    print(f"[Backtest] Fills created:   {result.fills_created:,}")
    print(f"[Backtest] Snapshots:       {result.snapshots_written:,}")
    print(f"[Backtest] Final equity:    ${float(result.final_equity):>12,.2f}")
    print(f"[Backtest] Final cash:      ${float(result.final_cash):>12,.2f}")
    pnl = result.final_equity - cfg.initial_capital
    pnl_pct = float(pnl) / float(cfg.initial_capital) * 100
    sign = "+" if pnl >= 0 else ""
    print(f"[Backtest] Total PnL:       {sign}${float(pnl):,.2f}  ({sign}{pnl_pct:.2f}%)")
    return 0


def handle_soak_loop_paper(args: argparse.Namespace) -> int:
    return _PaperTradingSoakRunner(mode=args.mode).run()


def handle_soak_loop_research(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    start = parse_datetime(args.start)
    end = parse_datetime(args.end)

    experiment_plan: ExperimentDefinition | None = None
    if args.experiment_plan:
        plan_path = Path(args.experiment_plan)
        if not plan_path.exists():
            print_error(f"Experiment plan file not found: {plan_path}")
            return 1
        session = get_session()
        try:
            ctx = build_simulation_context(session=session)
            experiment_plan = _load_experiment_plan(plan_path, ctx)
        except Exception as exc:
            print_error(f"Failed to load experiment plan: {exc}")
            return 1
        finally:
            session.close()

    return _ResearchSoakRunner(
        symbols=symbols,
        start=start,
        end=end,
        loop=args.loop,
        experiment_plan=experiment_plan,
    ).run()
