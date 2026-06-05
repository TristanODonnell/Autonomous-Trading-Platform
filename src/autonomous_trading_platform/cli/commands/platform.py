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

    # ── plan ──────────────────────────────────────────────────────────
    plan_parser = backtest_sub.add_parser(
        "plan",
        help="Validate and print the intended backtest plan without mutation.",
    )
    plan_parser.add_argument(
        "--fixture",
        type=Path,
        help="Path to a platform_replay YAML fixture file.",
    )
    plan_parser.add_argument(
        "--symbols",
        help="Comma-separated symbols, e.g. SPY,QQQ (overrides fixture)",
    )
    plan_parser.add_argument(
        "--start",
        help="Start date YYYY-MM-DD (overrides fixture)",
    )
    plan_parser.add_argument(
        "--end",
        help="End date YYYY-MM-DD (overrides fixture)",
    )
    plan_parser.add_argument("--starting-cash", type=Decimal, default=None)
    plan_parser.add_argument("--random-seed", type=int, default=None)
    plan_parser.add_argument(
        "--output",
        type=Path,
        help="Optional: validate that this output path will be writable.",
    )
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.set_defaults(func=handle_backtest_plan)

    # ── run ───────────────────────────────────────────────────────────
    run_parser = backtest_sub.add_parser(
        "run",
        help="Run canonical end-to-end historical backtest and emit artifact bundle.",
    )
    run_parser.add_argument(
        "--fixture",
        type=Path,
        help="Path to a platform_replay YAML fixture file.",
    )
    run_parser.add_argument(
        "--symbols",
        help="Comma-separated symbols (overrides fixture)",
    )
    run_parser.add_argument(
        "--start",
        help="Start date YYYY-MM-DD (overrides fixture)",
    )
    run_parser.add_argument(
        "--end",
        help="End date YYYY-MM-DD (overrides fixture)",
    )
    run_parser.add_argument("--starting-cash", type=Decimal, default=None)
    run_parser.add_argument("--random-seed", type=int, default=None)
    run_parser.add_argument(
        "--cadence-minutes",
        type=int,
        default=390,
        help="Tick cadence in minutes (default: 390 = full trading day)",
    )
    run_parser.add_argument(
        "--actor",
        default="platform-backtest",
        help="Actor identifier recorded in audit trail",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the replay without executing any mutations",
    )
    run_parser.add_argument(
        "--inject-failures",
        action="store_true",
        help="Enable failure injections defined in the fixture timeline_events",
    )
    run_parser.add_argument(
        "--output",
        type=Path,
        help="Write artifact bundle JSON to this path",
    )
    run_parser.set_defaults(func=handle_backtest_run)

    # ── inspect ───────────────────────────────────────────────────────
    inspect_parser = backtest_sub.add_parser(
        "inspect",
        help="Inspect a saved platform backtest artifact bundle.",
    )
    _inspect_group = inspect_parser.add_mutually_exclusive_group(required=True)
    _inspect_group.add_argument(
        "--artifact",
        type=Path,
        help="Path to artifact bundle JSON produced by 'platform backtest run'",
    )
    _inspect_group.add_argument(
        "--run-id",
        help="Replay run_id — searches artifacts/platform/backtests/ for a matching bundle",
    )
    inspect_parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/platform/backtests"),
        help="Directory to search when --run-id is used (default: artifacts/platform/backtests)",
    )
    inspect_parser.add_argument(
        "--section",
        default=None,
        help="Print only one section: runtime, risk, governance, portfolio, settings, etc.",
    )
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(func=handle_backtest_inspect)

    # ── report ────────────────────────────────────────────────────────
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
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {raw!r} — use YYYY-MM-DD") from exc


def _load_fixture_optional(fixture_path: Path | None):
    """Load fixture if provided, return None otherwise."""
    if fixture_path is None:
        return None
    from autonomous_trading_platform.platform.replay.platform_replay_config import load_fixture

    return load_fixture(fixture_path)


def _merge_params(fixture, args):
    """Merge fixture + CLI args into MergedReplayParams. Returns (params, error_str)."""
    from autonomous_trading_platform.platform.replay.platform_replay_config import (
        merge_fixture_with_cli,
    )

    raw_symbols = getattr(args, "symbols", None)
    cli_symbols = _parse_symbols(raw_symbols) if raw_symbols else None

    try:
        params = merge_fixture_with_cli(
            fixture=fixture,
            cli_symbols=cli_symbols,
            cli_start=getattr(args, "start", None),
            cli_end=getattr(args, "end", None),
            cli_starting_cash=getattr(args, "starting_cash", None),
            cli_random_seed=getattr(args, "random_seed", None),
        )
    except ValueError as exc:
        return None, str(exc)
    return params, None


# ---------------------------------------------------------------------------
# backtest plan
# ---------------------------------------------------------------------------


def handle_backtest_plan(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.platform.replay.platform_replay_config import (
        validate_plan,
    )

    try:
        fixture = _load_fixture_optional(getattr(args, "fixture", None))
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc))
        return 1

    params, err = _merge_params(fixture, args)
    if err:
        print_error(err)
        return 1

    output_path: Path | None = getattr(args, "output", None)
    plan = validate_plan(params, output_path=output_path)

    print_header("Platform Backtest Plan")
    print_json(plan)
    return 0 if plan.get("valid", True) else 1


# ---------------------------------------------------------------------------
# backtest run
# ---------------------------------------------------------------------------


def handle_backtest_run(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.platform_backtest_service import (
        PlatformBacktestInputs,
        PlatformBacktestRunner,
    )
    from autonomous_trading_platform.platform.replay.platform_replay_config import (
        build_typed_timeline_events,
    )

    try:
        fixture = _load_fixture_optional(getattr(args, "fixture", None))
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc))
        return 1

    params, err = _merge_params(fixture, args)
    if err:
        print_error(err)
        return 1

    dry_run: bool = getattr(args, "dry_run", False)
    actor: str = getattr(args, "actor", "platform-backtest")
    inject_failures: bool = getattr(args, "inject_failures", False)
    output: Path | None = getattr(args, "output", None)
    artifact_dir = output.parent if output else None

    # Build typed timeline events from fixture
    typed_timeline = (
        build_typed_timeline_events(params.timeline_events, actor=actor)
        if params.timeline_events
        else []
    )

    inputs = PlatformBacktestInputs(
        symbols=params.symbols,
        start_date=params.start_date,
        end_date=params.end_date,
        starting_cash=params.starting_cash,
        random_seed=params.random_seed,
        cadence_minutes=getattr(args, "cadence_minutes", 390),
        actor=actor,
        dry_run=dry_run,
        artifact_dir=artifact_dir,
        timeline=typed_timeline,
        inject_failures=inject_failures,
        failure_injection_schedule=params.failure_injections,
        fixture_name=fixture.platform_replay.name if fixture else None,
        scheduled_jobs_config={
            name: {"cadence": cfg.cadence, "enabled": cfg.enabled}
            for name, cfg in params.scheduled_jobs.items()
        },
    )

    print_header(f"Platform Backtest Run {'(dry-run)' if dry_run else ''}")

    if dry_run:
        from autonomous_trading_platform.platform.replay.platform_replay_config import (
            validate_plan,
        )

        plan = validate_plan(params, output_path=output)
        print_json(plan)
        return 0

    try:
        runner = PlatformBacktestRunner()
        artifact = runner.run(inputs)
        bundle = artifact.to_dict()
    except Exception as exc:
        print_error(f"Backtest run failed: {exc}")
        return 1

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
        print(f"Artifact bundle saved: {output}")

    print_json(
        {
            "status": "ok" if not artifact.errors else "completed_with_errors",
            "replay_id": artifact.replay_id,
            "run_id": artifact.run_id,
            "fixture_name": artifact.fixture_name,
            "symbols": artifact.symbols,
            "start_date": artifact.start_date,
            "end_date": artifact.end_date,
            "inject_failures": artifact.inject_failures,
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


# ---------------------------------------------------------------------------
# backtest inspect
# ---------------------------------------------------------------------------


def handle_backtest_inspect(args: argparse.Namespace) -> int:
    artifact_path: Path | None = getattr(args, "artifact", None)
    run_id: str | None = getattr(args, "run_id", None)

    if run_id and not artifact_path:
        artifacts_dir: Path = getattr(args, "artifacts_dir", Path("artifacts/platform/backtests"))
        artifact_path = _find_artifact_by_run_id(run_id, artifacts_dir)
        if artifact_path is None:
            print_error(
                f"No artifact found for run_id={run_id!r} in {artifacts_dir}. "
                "Use --artifact to specify the path directly."
            )
            return 1

    if not artifact_path or not artifact_path.exists():
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
        summary = {k: v for k, v in bundle.items() if k != "tick_results"}
        summary["tick_count"] = len(bundle.get("tick_results", []))
        print_json(summary)

    return 0


def _find_artifact_by_run_id(run_id: str, artifacts_dir: Path) -> Path | None:
    """Search artifacts_dir for a bundle JSON whose run_id or replay_id matches."""
    if not artifacts_dir.exists():
        return None
    for p in sorted(artifacts_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("run_id") == run_id or data.get("replay_id") == run_id:
                return p
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# backtest report
# ---------------------------------------------------------------------------


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

    # Count failure injections from tick_results
    failure_injections_applied = sum(
        1
        for tick in bundle.get("tick_results", [])
        for ev in tick.get("timeline_events", [])
        if ev.get("event_type") == "failure_injected"
    )
    timeline_events_applied = len(bundle.get("timeline_events_applied", []))

    report = {
        "replay_id": bundle.get("replay_id"),
        "run_id": bundle.get("run_id"),
        "fixture_name": bundle.get("fixture_name"),
        "symbols": bundle.get("symbols"),
        "start_date": bundle.get("start_date"),
        "end_date": bundle.get("end_date"),
        "dry_run": bundle.get("dry_run"),
        "inject_failures": bundle.get("inject_failures", False),
        "started_at": bundle.get("started_at"),
        "completed_at": bundle.get("completed_at"),
        "ticks_attempted": runtime.get("ticks_attempted", 0),
        "ticks_ok": runtime.get("ticks_ok", 0),
        "ticks_failed": runtime.get("ticks_failed", 0),
        "total_orders": runtime.get("total_orders", 0),
        "total_fills": runtime.get("total_fills", 0),
        "cycles_run": runtime.get("ticks_ok", 0),
        "timeline_events_applied": timeline_events_applied,
        "failure_injections_applied": failure_injections_applied,
        "final_portfolio_value": portfolio.get("portfolio_value"),
        "final_cash_balance": portfolio.get("cash_balance"),
        "open_positions": portfolio.get("open_positions"),
        "total_pnl_pct": portfolio.get("total_pnl_pct"),
        "risk_blocked": risk.get("is_blocked", False),
        "risk_block_reasons": risk.get("block_reasons", []),
        "drawdown_pct": risk.get("drawdown_pct"),
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
