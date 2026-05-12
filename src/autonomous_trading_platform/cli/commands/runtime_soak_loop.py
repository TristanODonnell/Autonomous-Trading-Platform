from __future__ import annotations

import argparse
import contextlib
import json
import signal
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autonomous_trading_platform.cli.formatters import print_error, print_header
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
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
_MARKET_OPEN_H, _MARKET_OPEN_M = 9, 30
_MARKET_CLOSE_H, _MARKET_CLOSE_M = 16, 0
_EOD_WINDOW_H, _EOD_WINDOW_M = 18, 0
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


def _market_phase(now_et: datetime) -> str:
    if now_et.weekday() >= 5:
        return "WEEKEND"
    market_open = now_et.replace(
        hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0
    )
    market_close = now_et.replace(
        hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0
    )
    if now_et < market_open:
        return "PRE_MARKET"
    if now_et < market_close:
        return "MARKET_HOURS"
    return "POST_MARKET"


def _seconds_until_next_market_open(now_et: datetime) -> int:
    candidate = now_et
    for _ in range(8):
        candidate += timedelta(days=1)
        if candidate.weekday() < 5:
            next_open = candidate.replace(
                hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0
            )
            return max(0, int((next_open - now_et).total_seconds()))
    return 86400


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


def _load_experiment_plan(path: Path) -> ExperimentDefinition:
    with path.open() as f:
        raw: dict[str, object] = json.load(f)
    raw["experiment_type"] = ExperimentType(str(raw["experiment_type"]))
    raw["price_basis"] = PriceBasis(str(raw["price_basis"]))
    raw["start_date"] = date.fromisoformat(str(raw["start_date"]))
    raw["end_date"] = date.fromisoformat(str(raw["end_date"]))
    # staged_pipeline_config cannot be deserialized from plain JSON
    raw.pop("staged_pipeline_config", None)
    return ExperimentDefinition(**raw)  # type: ignore[arg-type]


class _PaperTradingSoakRunner:
    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._lock = InMemoryNoOverlapLock()
        self._shutdown = False
        self._intraday_cycles = 0
        self._eod_cycles = 0
        self._locks_acquired = 0
        self._locks_skipped = 0
        self._eod_done_for: date | None = None

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._on_signal)
        with contextlib.suppress(OSError, ValueError):
            signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum: int, _frame: object) -> None:
        name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n[SoakTestRunner] Received {name}, shutting down gracefully...")
        self._shutdown = True

    def _interruptible_sleep(self, seconds: int) -> None:
        end = time.monotonic() + seconds
        while not self._shutdown:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))

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
            result = orchestrator.run_intraday_tick(now_utc=_now_utc())
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
            result = orchestrator.run_eod_maintenance(now_utc=_now_utc())
            self._eod_done_for = _now_et().date()
            print("  Lock released")
            print(f"  ✓ Completed (correlation_id: {result.correlation_id})")
            self._eod_cycles += 1
        except Exception as exc:
            print(f"  ⚠ Error during EOD maintenance: {exc}")
        finally:
            session.close()
            self._lock.release(_EOD_LOCK_KEY)

    def _eod_eligible(self, now_et: datetime) -> bool:
        if now_et.weekday() >= 5 or _market_phase(now_et) != "POST_MARKET":
            return False
        eod_threshold = now_et.replace(
            hour=_EOD_WINDOW_H, minute=_EOD_WINDOW_M, second=0, microsecond=0
        )
        return now_et >= eod_threshold and self._eod_done_for != now_et.date()

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

    def run(self) -> int:
        self._install_signal_handlers()
        mode_label = {
            "fast": "fast mode",
            "realistic": "realistic mode",
            "single": "single tick",
        }.get(self._mode, self._mode)
        print_header(f"Paper Trading Soak Loop — {mode_label}")
        print("[SoakTestRunner] WARNING: This makes real API calls to Alpaca paper trading")
        print("[SoakTestRunner] Lock manager: InMemoryNoOverlapLock")

        if self._mode == "single":
            return self._run_single()
        interval = 0 if self._mode == "fast" else _INTRADAY_INTERVAL_SECONDS
        return self._run_loop(intraday_interval=interval)

    def _run_single(self) -> int:
        now_et = _now_et()
        phase = _market_phase(now_et)
        print(f"[SoakTestRunner] Market phase: {phase}")
        if phase != "MARKET_HOURS":
            print("[SoakTestRunner] Market is not open — single mode requires MARKET_HOURS")
            return 1
        self._run_intraday_tick()
        self._print_stats()
        return 0

    def _run_loop(self, intraday_interval: int) -> int:
        last_phase: str | None = None
        waiting_for: str | None = None

        while not self._shutdown:
            now_et = _now_et()
            phase = _market_phase(now_et)

            if phase != last_phase:
                if last_phase == "MARKET_HOURS" and phase == "POST_MARKET":
                    print(f"[{_ts()}] Market phase: POST_MARKET")
                    print(f"[{_ts()}] Market closed — stopping intraday ticks")
                else:
                    print(f"[{_ts()}] Market phase: {phase}")
                last_phase = phase
                waiting_for = None

            if phase == "MARKET_HOURS":
                self._run_intraday_tick()
                if not self._shutdown and intraday_interval > 0:
                    close_et = now_et.replace(
                        hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0
                    )
                    secs_to_close = max(0, int((close_et - _now_et()).total_seconds()))
                    sleep_secs = min(intraday_interval, secs_to_close)
                    if sleep_secs > 0:
                        next_tick = (_now_et() + timedelta(seconds=sleep_secs)).strftime("%H:%M:%S")
                        print(
                            f"[SoakTestRunner] Sleeping {sleep_secs}s until next tick "
                            f"({next_tick} ET)..."
                        )
                        self._interruptible_sleep(sleep_secs)
            elif self._eod_eligible(now_et):
                waiting_for = None
                self._run_eod_maintenance()
            else:
                eod_threshold = now_et.replace(
                    hour=_EOD_WINDOW_H, minute=_EOD_WINDOW_M, second=0, microsecond=0
                )
                if (
                    phase == "POST_MARKET"
                    and now_et < eod_threshold
                    and now_et.weekday() < 5
                    and self._eod_done_for != now_et.date()
                ):
                    secs = max(0, int((eod_threshold - now_et).total_seconds()))
                    if waiting_for != "eod":
                        print(
                            f"[SoakTestRunner] EOD maintenance at 18:00 ET "
                            f"(in {_fmt_duration(secs)}), sleeping..."
                        )
                        waiting_for = "eod"
                    self._interruptible_sleep(min(secs, 60))
                else:
                    secs = _seconds_until_next_market_open(now_et)
                    if waiting_for != "market_open":
                        print(
                            f"[SoakTestRunner] Next market open in "
                            f"{_fmt_duration(secs)}, sleeping..."
                        )
                        waiting_for = "market_open"
                    self._interruptible_sleep(min(secs, 300))

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
        self._shutdown = False
        self._cycles = 0

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._on_signal)
        with contextlib.suppress(OSError, ValueError):
            signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum: int, _frame: object) -> None:
        name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n[SoakTestRunner] Received {name}, shutting down gracefully...")
        self._shutdown = True

    def run(self) -> int:
        self._install_signal_handlers()
        mode_label = "loop mode" if self._loop else "one-shot"
        print_header(f"Historical Research Soak Loop — {mode_label}")
        print(f"[SoakTestRunner] Symbols: {', '.join(self._symbols)}")
        print(f"[SoakTestRunner] Period: {self._start.date()} to {self._end.date()}")
        if self._loop:
            print(
                "[SoakTestRunner] WARNING: This will repeatedly backfill the same period "
                "(soak test)"
            )

        while not self._shutdown:
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
        help="Path to experiment plan JSON file (optional)",
    )
    research_parser.set_defaults(func=handle_soak_loop_research)


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
        try:
            experiment_plan = _load_experiment_plan(plan_path)
        except Exception as exc:
            print_error(f"Failed to load experiment plan: {exc}")
            return 1

    return _ResearchSoakRunner(
        symbols=symbols,
        start=start,
        end=end,
        loop=args.loop,
        experiment_plan=experiment_plan,
    ).run()
