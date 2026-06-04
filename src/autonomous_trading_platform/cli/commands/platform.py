from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json


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
    plan_parser.add_argument("--starting-cash", type=Decimal, default=Decimal("100000"))
    plan_parser.add_argument("--random-seed", type=int, default=42)
    plan_parser.add_argument(
        "--cadence-minutes",
        type=int,
        default=390,
        help="Tick cadence in minutes (default: 390 = full trading day)",
    )
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
    run_parser.add_argument(
        "--cadence-minutes",
        type=int,
        default=390,
        help="Tick cadence in minutes (default: 390 = full trading day)",
    )
    run_parser.add_argument(
        "--actor", default="platform-backtest", help="Actor identifier recorded in audit trail"
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Plan the replay without executing any mutations"
    )
    run_parser.add_argument("--output", type=Path, help="Write artifact bundle JSON to this path")
    run_parser.set_defaults(func=handle_backtest_run)

    inspect_parser = backtest_sub.add_parser(
        "inspect",
        help="Inspect a saved platform backtest artifact bundle.",
    )
    inspect_parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="Path to artifact bundle JSON produced by 'platform backtest run'",
    )
    inspect_parser.add_argument(
        "--section",
        default=None,
        help="Print only one section: runtime, risk, governance, portfolio, settings, etc.",
    )
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(func=handle_backtest_inspect)

    report_parser = backtest_sub.add_parser(
        "report",
        help="Summarize completed backtest artifact bundle for humans or CI.",
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
# backtest handlers
# ---------------------------------------------------------------------------


def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {raw!r} — use YYYY-MM-DD") from exc


def handle_backtest_plan(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.platform_backtest_service import (
        PlatformBacktestInputs,
        PlatformBacktestRunner,
    )

    try:
        symbols = _parse_symbols(args.symbols)
        start = _parse_date(args.start)
        end = _parse_date(args.end)
    except Exception as exc:
        print_error(str(exc))
        return 1

    if start >= end:
        print_error(f"--start ({start}) must be before --end ({end})")
        return 1

    inputs = PlatformBacktestInputs(
        symbols=symbols,
        start_date=start,
        end_date=end,
        starting_cash=getattr(args, "starting_cash", Decimal("100000")),
        random_seed=getattr(args, "random_seed", 42),
        cadence_minutes=getattr(args, "cadence_minutes", 390),
        dry_run=True,
    )

    plan = PlatformBacktestRunner().plan(inputs)
    print_header("Platform Backtest Plan")
    print_json(plan)
    return 0


def handle_backtest_run(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.platform_backtest_service import (
        PlatformBacktestInputs,
        PlatformBacktestRunner,
    )

    try:
        symbols = _parse_symbols(args.symbols)
        start = _parse_date(args.start)
        end = _parse_date(args.end)
    except Exception as exc:
        print_error(str(exc))
        return 1

    if start >= end:
        print_error(f"--start ({start}) must be before --end ({end})")
        return 1

    dry_run = getattr(args, "dry_run", False)
    actor = getattr(args, "actor", "platform-backtest")
    output: Path | None = getattr(args, "output", None)
    artifact_dir = output.parent if output else None

    inputs = PlatformBacktestInputs(
        symbols=symbols,
        start_date=start,
        end_date=end,
        starting_cash=getattr(args, "starting_cash", Decimal("100000")),
        random_seed=getattr(args, "random_seed", 42),
        cadence_minutes=getattr(args, "cadence_minutes", 390),
        actor=actor,
        dry_run=dry_run,
        artifact_dir=artifact_dir,
    )

    print_header(f"Platform Backtest Run {'(dry-run)' if dry_run else ''}")

    try:
        runner = PlatformBacktestRunner()
        if dry_run:
            plan = runner.plan(inputs)
            print_json(plan)
            return 0

        artifact = runner.run(inputs)
        bundle = artifact.to_dict()
    except Exception as exc:
        print_error(f"Backtest run failed: {exc}")
        return 1

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
        print(f"Artifact bundle saved: {output}")

    # Print a compact summary — not the full bundle
    print_json(
        {
            "status": "ok" if not artifact.errors else "completed_with_errors",
            "replay_id": artifact.replay_id,
            "run_id": artifact.run_id,
            "symbols": artifact.symbols,
            "start_date": artifact.start_date,
            "end_date": artifact.end_date,
            "ticks_attempted": artifact.runtime.ticks_attempted if artifact.runtime else 0,
            "ticks_ok": artifact.runtime.ticks_ok if artifact.runtime else 0,
            "ticks_failed": artifact.runtime.ticks_failed if artifact.runtime else 0,
            "total_orders": artifact.runtime.total_orders if artifact.runtime else 0,
            "errors": artifact.errors[:10],
            "warnings": artifact.warnings[:10],
            "artifact_path": str(output) if output else None,
        }
    )
    return 0 if not artifact.errors else 1


def handle_backtest_inspect(args: argparse.Namespace) -> int:
    artifact_path: Path = args.artifact
    if not artifact_path.exists():
        print_error(f"Artifact not found: {artifact_path}")
        return 1

    try:
        bundle = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print_error(f"Failed to read artifact: {exc}")
        return 1

    section = getattr(args, "section", None)
    print_header(f"Platform Backtest Inspect{f' — {section}' if section else ''}")

    if section:
        if section not in bundle:
            print_error(f"Section not found: {section!r}. Available: {sorted(bundle.keys())}")
            return 1
        print_json({section: bundle[section]})
    else:
        # Print top-level summary without full tick_results
        summary = {k: v for k, v in bundle.items() if k != "tick_results"}
        summary["tick_count"] = len(bundle.get("tick_results", []))
        print_json(summary)

    return 0


def handle_backtest_report(args: argparse.Namespace) -> int:
    artifact_path: Path = args.artifact
    if not artifact_path.exists():
        print_error(f"Artifact not found: {artifact_path}")
        return 1

    try:
        bundle = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print_error(f"Failed to read artifact: {exc}")
        return 1

    runtime = bundle.get("runtime") or {}
    portfolio = bundle.get("portfolio") or {}
    risk = bundle.get("risk") or {}
    governance = bundle.get("governance") or {}
    errors = bundle.get("errors") or []
    warnings = bundle.get("warnings") or []

    report = {
        "replay_id": bundle.get("replay_id"),
        "symbols": bundle.get("symbols"),
        "start_date": bundle.get("start_date"),
        "end_date": bundle.get("end_date"),
        "dry_run": bundle.get("dry_run"),
        "started_at": bundle.get("started_at"),
        "completed_at": bundle.get("completed_at"),
        "ticks_attempted": runtime.get("ticks_attempted", 0),
        "ticks_ok": runtime.get("ticks_ok", 0),
        "ticks_failed": runtime.get("ticks_failed", 0),
        "total_orders": runtime.get("total_orders", 0),
        "total_fills": runtime.get("total_fills", 0),
        "final_portfolio_value": portfolio.get("portfolio_value"),
        "final_cash_balance": portfolio.get("cash_balance"),
        "open_positions": portfolio.get("open_positions"),
        "total_pnl_pct": portfolio.get("total_pnl_pct"),
        "risk_blocked": risk.get("is_blocked", False),
        "risk_block_reasons": risk.get("block_reasons", []),
        "governance_strategies_in_breach": governance.get("strategies_in_breach", []),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors_sample": errors[:5],
        "pass": len(errors) == 0,
    }

    print_header("Platform Backtest Report")
    print_json(report)
    return 0 if report["pass"] else 1


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
