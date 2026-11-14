from __future__ import annotations

import argparse
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.db import get_session, rollback_only_session
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    EodStepRefused,
    PaperTradingGoldenPathOrchestrator,
)

_DESCRIPTION = """\
Run one step of the end-of-day chain for a trading date, outside the schedule: to
rehearse a weekly or monthly step before it fires, or to redo a step after a fix.

It is the scheduler's own step function. The step sees the clock the chain has on
--as-of (18:00 ET), and steps that need the day's raw-bars dataset resolve it first,
as they do in the chain. The step's calendar gate is reported, not enforced.

--dry-run runs the step in a database transaction that is rolled back, so nothing is
kept, including commits the step makes. Steps with effects outside that transaction
(final_ingestion, corporate_actions, features, monthly_universe_rotation,
rolling_raw_bars, publish_datasets) have no dry run here and refuse the flag; the
rotation is rehearsed with `atp universe monthly-rotation --dry-run`.

A real run is refused when the scheduled chain is running, when the chain already
completed the step for that date (override: --force), and, off its scheduled day, for
monthly_universe_rotation (always) and weekly_portfolio_review in auto mode (use
--dry-run).

A dry run holds locks on the rows the step touches until it ends. Run it outside
market hours on a box that is trading.
"""


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--as-of must be a date (YYYY-MM-DD): {value}") from exc


def register_run_eod_step(runtime_subparsers) -> None:
    parser = runtime_subparsers.add_parser(
        "run-eod-step",
        help="Run one end-of-day chain step on demand (rehearsal or repair)",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "step",
        help="Step name, e.g. rolling_raw_bars. An unknown name prints the list",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        required=True,
        type=_iso_date,
        help="Trading date to run the step for (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the step and roll everything back; only for rollback-safe steps",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the chain already completed this step for the date",
    )
    parser.add_argument(
        "--actor",
        help="Operator identity recorded on the job run",
    )
    parser.set_defaults(func=handle_run_eod_step)


@contextmanager
def _session_for(*, dry_run: bool) -> Generator[Session, None, None]:
    if dry_run:
        with rollback_only_session() as session:
            yield session
        return
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def handle_run_eod_step(args: argparse.Namespace) -> int:
    dry_run = bool(args.dry_run)
    with _session_for(dry_run=dry_run) as session:
        try:
            result = PaperTradingGoldenPathOrchestrator(session).run_eod_step(
                args.step,
                trading_date=args.as_of,
                dry_run=dry_run,
                force=bool(args.force),
                actor=args.actor,
            )
        except EodStepRefused as exc:
            print_error(str(exc))
            return 2

    print_header("Run EOD Step (dry run, rolled back)" if dry_run else "Run EOD Step")
    print_json(
        {
            "step": result.step,
            "trading_date": result.trading_date.isoformat(),
            "now_utc": result.now_utc.isoformat(),
            "dry_run": result.dry_run,
            "scheduled_for_date": result.scheduled_for_date,
            "status": result.status,
            # A dry run's job-run row is rolled back with everything else.
            "job_run_id": None if dry_run else result.job_run_id,
            "output": result.output,
            "error": result.error,
        }
    )
    return 0 if result.status == "completed" else 1
