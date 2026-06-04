from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from autonomous_trading_platform.cli.formatters import print_header, print_json


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "platform",
        help="Platform-level workflows: backtesting, fixture seeding, dashboard snapshots.",
    )
    platform_sub = parser.add_subparsers(dest="platform_command", required=True)

    # ------------------------------------------------------------------
    # backtest sub-group
    # ------------------------------------------------------------------
    backtest_parser = platform_sub.add_parser(
        "backtest",
        help="Historical backtest workflows.",
    )
    backtest_sub = backtest_parser.add_subparsers(dest="backtest_command", required=True)

    plan_parser = backtest_sub.add_parser(
        "plan",
        help="Validate and print the intended backtest plan without mutation.",
    )
    plan_parser.add_argument(
        "--symbols", required=True, help="Comma-separated symbols, e.g. SPY,QQQ"
    )
    plan_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    plan_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.set_defaults(func=handle_backtest_plan)

    run_parser = backtest_sub.add_parser(
        "run",
        help="Run canonical end-to-end historical backtest and emit artifact bundle.",
    )
    run_parser.add_argument("--symbols", required=True)
    run_parser.add_argument("--start", required=True)
    run_parser.add_argument("--end", required=True)
    run_parser.add_argument("--starting-cash", type=Decimal, default=Decimal("100000"))
    run_parser.add_argument("--random-seed", type=int, default=42)
    run_parser.add_argument("--output", type=Path)
    run_parser.set_defaults(func=handle_backtest_run)

    inspect_parser = backtest_sub.add_parser(
        "inspect",
        help="Inspect a saved platform backtest run or artifact.",
    )
    inspect_parser.add_argument("--run-id", required=True)
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(func=handle_backtest_inspect)

    report_parser = backtest_sub.add_parser(
        "report",
        help="Summarize completed backtest artifacts for humans or CI.",
    )
    report_parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="Path to the backtest artifact bundle JSON.",
    )
    report_parser.add_argument("--json", action="store_true")
    report_parser.set_defaults(func=handle_backtest_report)

    # ------------------------------------------------------------------
    # fixture seed
    # ------------------------------------------------------------------
    fixture_parser = platform_sub.add_parser(
        "fixture",
        help="Manage multi-domain scenario fixtures.",
    )
    fixture_sub = fixture_parser.add_subparsers(dest="fixture_command", required=True)

    fixture_seed = fixture_sub.add_parser(
        "seed",
        help=(
            "Seed multi-domain scenario fixture: strategies, governance, controls, "
            "allocations, and settings from a YAML file."
        ),
    )
    fixture_seed.add_argument(
        "--fixture",
        required=True,
        type=Path,
        help="Path to the YAML fixture file.",
    )
    fixture_seed.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be seeded without writing to the DB.",
    )
    fixture_seed.add_argument(
        "--actor",
        default="platform-cli",
        help="Actor identifier for audit logging.",
    )
    fixture_seed.add_argument("--reason", default="platform fixture seed")
    fixture_seed.set_defaults(func=handle_fixture_seed)

    # ------------------------------------------------------------------
    # dashboard-snapshot
    # ------------------------------------------------------------------
    dashboard_parser = platform_sub.add_parser(
        "dashboard-snapshot",
        help="Export a dashboard/API validation snapshot (portfolio, strategies, risk, equity curve).",
    )
    dashboard_parser.add_argument("--format", choices=["json"], default="json")
    dashboard_parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the snapshot artifact.",
    )
    dashboard_parser.set_defaults(func=handle_dashboard_snapshot)


# ---------------------------------------------------------------------------
# backtest handlers (stubs — replace when canonical runner is implemented)
# ---------------------------------------------------------------------------


def handle_backtest_plan(args: argparse.Namespace) -> int:
    print_header("Platform Backtest Plan")
    print_json(
        {
            "status": "not_implemented",
            "note": (
                "platform backtest plan will validate dataset availability, universe coverage, "
                "and feature pipeline readiness without running a replay."
            ),
            "symbols": args.symbols,
            "start": args.start,
            "end": args.end,
        }
    )
    return 0


def handle_backtest_run(args: argparse.Namespace) -> int:
    print_header("Platform Backtest Run")
    print_json(
        {
            "status": "not_implemented",
            "note": (
                "platform backtest run will execute the canonical end-to-end historical workflow "
                "via BacktestTradingCycleOrchestrator and emit a platform artifact bundle."
            ),
            "symbols": args.symbols,
            "start": args.start,
            "end": args.end,
        }
    )
    return 0


def handle_backtest_inspect(args: argparse.Namespace) -> int:
    print_header("Platform Backtest Inspect")
    print_json(
        {
            "status": "not_implemented",
            "run_id": args.run_id,
            "note": (
                "platform backtest inspect will read run manifests, simulation runs, "
                "and artifact bundle metadata for the given run_id."
            ),
        }
    )
    return 0


def handle_backtest_report(args: argparse.Namespace) -> int:
    artifact_path: Path = args.artifact
    if not artifact_path.exists():
        from autonomous_trading_platform.cli.formatters import print_error

        print_error(f"Artifact not found: {artifact_path}")
        return 1
    print_header("Platform Backtest Report")
    print_json(
        {
            "status": "not_implemented",
            "artifact": str(artifact_path),
            "note": "platform backtest report will summarize completed artifact bundles for CI/operator review.",
        }
    )
    return 0


# ---------------------------------------------------------------------------
# fixture seed handler
# ---------------------------------------------------------------------------


def handle_fixture_seed(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import handle_seed_fixture

    return handle_seed_fixture(args)


# ---------------------------------------------------------------------------
# dashboard snapshot handler
# ---------------------------------------------------------------------------


def handle_dashboard_snapshot(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import handle_read_dashboard

    result = handle_read_dashboard(args)

    if getattr(args, "output", None) and result == 0:
        import json
        from datetime import UTC, datetime

        from autonomous_trading_platform.application.services.active_strategies_service import (
            ActiveStrategiesService,
        )
        from autonomous_trading_platform.application.services.portfolio_analytics_service import (
            PortfolioAnalyticsService,
        )
        from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
            PortfolioEquityCurveService,
        )
        from autonomous_trading_platform.application.services.portfolio_summary_service import (
            PortfolioSummaryService,
        )
        from autonomous_trading_platform.db import get_session

        output: Path = args.output
        session = get_session()
        try:
            summary = PortfolioSummaryService(session=session).get_summary()
            active_strategies = ActiveStrategiesService(session=session).list_active_strategies()
            risk = PortfolioAnalyticsService(session=session).get_risk()
            perf = PortfolioAnalyticsService(session=session).get_performance()
            curve_1m = PortfolioEquityCurveService(session=session).get_equity_curve("1m")
        finally:
            session.close()

        bundle = {
            "exported_at": datetime.now(UTC).isoformat(),
            "portfolio_summary": summary,
            "active_strategies": active_strategies,
            "risk": risk,
            "performance": perf,
            "equity_curve_1m_points": len(curve_1m.get("points", [])),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
        print(f"Dashboard snapshot saved: {output}")

    return result
