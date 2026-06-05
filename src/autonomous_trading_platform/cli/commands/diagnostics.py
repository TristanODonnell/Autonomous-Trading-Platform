from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.audit_log_service import AuditLogService
from autonomous_trading_platform.application.services.operations_service import OperationsService
from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.application.services.runtime_snapshot_service import (
    _ALL_SECTIONS,
    RuntimeSnapshotService,
)
from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.cli.formatters import print_json
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.runtime_snapshot import RuntimeSnapshot
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)


@dataclass
class DiagnosticsCliDependencies:
    session: Session


def build_dependencies() -> DiagnosticsCliDependencies:
    return DiagnosticsCliDependencies(session=get_session())


def _is_missing_table(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "no such table" in msg or "does not exist" in msg or "relation" in msg


def _db_not_ready(exc: Exception) -> int:
    if _is_missing_table(exc):
        print(
            "[diagnostics] DB tables not found. "
            "Run: docker compose up -d && alembic -c infra/db/alembic.ini upgrade head"
        )
    else:
        print(f"[diagnostics] DB error: {exc}")
    return 1


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

_VALID_SECTIONS = sorted(_ALL_SECTIONS)


def register(subparsers) -> None:
    diag_parser = subparsers.add_parser("diagnostics", help="Diagnostics commands")
    diag_subparsers = diag_parser.add_subparsers(
        dest="diagnostics_command",
        required=True,
    )

    # --- snapshot -----------------------------------------------------------
    snap = diag_subparsers.add_parser(
        "snapshot",
        help="Print current runtime state for debugging",
    )
    snap.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON instead of human-readable text",
    )
    snap.add_argument(
        "--local-only",
        action="store_true",
        dest="local_only",
        help="Use only local DB; never construct broker clients (default safe mode)",
    )
    snap.add_argument(
        "--include-broker",
        action="store_true",
        dest="include_broker",
        help="Allow broker-backed portfolio reads (overrides --local-only)",
    )
    snap.add_argument(
        "--output",
        dest="output",
        metavar="PATH",
        default=None,
        help="Write JSON snapshot to this file path",
    )
    snap.add_argument(
        "--section",
        dest="section",
        choices=_VALID_SECTIONS,
        default=None,
        metavar="SECTION",
        help=f"Capture only one section: {', '.join(_VALID_SECTIONS)}",
    )
    snap.set_defaults(func=handle_snapshot)

    # --- controls -----------------------------------------------------------
    diag_subparsers.add_parser(
        "controls",
        help="Print current operator and strategy controls (read-only)",
    ).set_defaults(func=handle_controls)

    # --- settings -----------------------------------------------------------
    diag_subparsers.add_parser(
        "settings",
        help="Print persisted operator settings (read-only)",
    ).set_defaults(func=handle_settings)

    # --- datasets -----------------------------------------------------------
    diag_subparsers.add_parser(
        "datasets",
        help="Show latest dataset version records (read-only)",
    ).set_defaults(func=handle_datasets)

    # --- activity -----------------------------------------------------------
    act = diag_subparsers.add_parser(
        "activity",
        help="Show recent audit/activity feed (read-only)",
    )
    act.add_argument(
        "--limit",
        type=int,
        default=20,
        dest="limit",
        help="Number of events to show (default: 20)",
    )
    act.set_defaults(func=handle_activity)

    # --- portfolio ----------------------------------------------------------
    pf = diag_subparsers.add_parser(
        "portfolio",
        help="Local DB-backed portfolio summary, holdings, and risk (read-only)",
    )
    pf.add_argument(
        "--local-only",
        action="store_true",
        dest="local_only",
        default=True,
        help="Use only local DB (default: true; use diagnostics snapshot --include-broker for broker reads)",
    )
    pf.set_defaults(func=handle_portfolio)

    # --- experiments --------------------------------------------------------
    exp = diag_subparsers.add_parser(
        "experiments",
        help="Show recent experiment statuses (read-only)",
    )
    exp.add_argument(
        "--limit",
        type=int,
        default=10,
        dest="limit",
        help="Number of experiments to show (default: 10)",
    )
    exp.set_defaults(func=handle_experiments)

    # --- runtime-jobs -------------------------------------------------------
    rj = diag_subparsers.add_parser(
        "runtime-jobs",
        help="Show recent runtime job evidence (read-only)",
    )
    rj.add_argument(
        "--limit",
        type=int,
        default=20,
        dest="limit",
        help="Number of jobs to show (default: 20)",
    )
    rj.set_defaults(func=handle_runtime_jobs)

    # --- recent-errors ------------------------------------------------------
    re_parser = diag_subparsers.add_parser(
        "recent-errors",
        help="Show failed run manifests and recent failure events (read-only)",
    )
    re_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        dest="limit",
        help="Number of failed runs to show (default: 20)",
    )
    re_parser.set_defaults(func=handle_recent_errors)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


_SECTION_PRINTERS = {
    "controls": lambda snap: _print_controls_section(snap),
    "settings": lambda snap: _print_settings_section(snap),
    "portfolio": lambda snap: _print_portfolio(snap),
    "allocations": lambda snap: _print_allocations_section(snap),
    "datasets": lambda snap: _print_datasets_section(snap),
    "experiments": lambda snap: _print_experiments_section(snap),
    "activity": lambda snap: _print_activity_section(snap),
}


def handle_snapshot(args: argparse.Namespace) -> int:
    local_only = args.local_only and not args.include_broker
    sections = frozenset({args.section}) if args.section else None

    deps = build_dependencies()
    try:
        service = RuntimeSnapshotService(session=deps.session, local_only=local_only)
        snapshot = service.capture(sections=sections)

        if args.output:
            _write_snapshot_file(snapshot, args.output)

        if args.as_json:
            print_json(snapshot.model_dump(mode="json"))
        elif args.section:
            _SECTION_PRINTERS[args.section](snapshot)
        else:
            _print_snapshot(snapshot)

        return 0
    finally:
        deps.session.close()


def handle_controls(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        state = RuntimeControlService(session=deps.session).get_controls_state()
        print("\nOPERATOR CONTROLS")
        if state.kill_switch_active:
            status = "HALTED  <- kill switch active"
        elif state.trading_paused:
            status = "PAUSED  <- soft pause active"
        elif not state.trading_enabled:
            status = "DISABLED  <- trading_enabled=false"
        else:
            status = "Active"
        print(f"  Trading Status:  {status}")
        print(f"  Kill Switch:     {_yn(state.kill_switch_active)}")
        print(f"  Trading Paused:  {_yn(state.trading_paused)}")
        print(f"  Trading Enabled: {_yn(state.trading_enabled)}")
        print(f"  Trading Mode:    {state.trading_mode}")
        if state.reason:
            print(f"  Reason:          {state.reason}")
        if state.updated_by:
            print(f"  Updated By:      {state.updated_by}")
        if state.updated_at:
            print(f"  Updated At:      {state.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")

        n = len(state.strategies)
        print(f"\nSTRATEGY CONTROLS ({n})")
        if not state.strategies:
            print("  (none)")
        else:
            for s in state.strategies:
                flag = "enabled" if s.enabled else "DISABLED"
                suffix = f"  ({s.reason})" if s.reason else ""
                print(f"  {s.strategy_id:<30}  {flag}{suffix}")

        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_settings(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        svc = OperatorSettingsService(
            settings_repo=OperatorSettingsRepository(deps.session),
            audit_log_repo=AuditLogRepository(deps.session),
        )
        s = svc.get_settings()
        print("\nOPERATOR SETTINGS")
        print(f"  Max Portfolio DD:    {s.max_drawdown_limit * 100:.1f}%")
        print(f"  Max Strategy DD:     {s.max_strategy_drawdown * 100:.1f}%")
        print(f"  Risk Tolerance:      {s.risk_tolerance}")
        print(f"  Per-Strategy Cap:    {s.per_strategy_cap * 100:.1f}%")
        print(f"  Target Vol:          {s.target_portfolio_volatility * 100:.1f}%")
        print(f"  Min Sharpe:          {s.min_sharpe_for_promotion:.2f}")
        print(f"  Min Paper Days:      {s.min_paper_trading_period_days}")
        print(f"  Rebalance:           {s.rebalance_frequency}")
        print(f"  Slippage Model:      {s.slippage_model}")
        print(f"  Transaction Cost:    {s.transaction_cost_model}")
        print(f"  Auto-Promote:        {_yn(s.auto_promote_enabled)}")
        print(f"  Auto-Demote:         {_yn(s.auto_demote_on_breach)}")
        print(f"  Drawdown Alerts:     {_yn(s.notify_drawdown_alerts)}")
        print(f"  Promotion Alerts:    {_yn(s.notify_strategy_promotion_events)}")
        print(f"  Pipeline Alerts:     {_yn(s.notify_pipeline_failures)}")
        print(f"  Portfolio DD Action: {s.portfolio_drawdown_action}")
        print(f"  Portfolio DD Mode:   {s.portfolio_drawdown_recovery_mode}")
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


_KNOWN_DATASETS = ["raw_bars", "adjusted_bars", "features"]


def handle_datasets(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        repo = DatasetVersionsRepository(deps.session)
        print("\nDATASETS")
        for name in _KNOWN_DATASETS:
            row = None
            for basis in (PriceBasis.ADJUSTED, PriceBasis.RAW):
                row = repo.get_latest_validated(dataset_name=name, price_basis=basis)
                if row:
                    break
            ver = row.dataset_version_id if row else "N/A"
            print(f"  {name:<18}  {ver}")

        feature_row = deps.session.scalars(
            select(FeatureDatasetVersions)
            .order_by(FeatureDatasetVersions.created_at.desc())
            .limit(1)
        ).first()
        feat_ver = feature_row.dataset_version_id if feature_row else "N/A"
        print(f"  {'feature_version':<18}  {feat_ver}")
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_activity(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        svc = AuditLogService(session=deps.session)
        result = svc.list_events(page=1, page_size=args.limit)
        print(f"\nRECENT ACTIVITY (last {len(result.events)} of {result.total})")
        if not result.events:
            print("  (none)")
        else:
            for event in result.events:
                time_str = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                actor = event.user or "system"
                desc = event.description or ""
                print(f"  {time_str}  {event.action_type:<40}  {actor:<20}  {desc}")
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_portfolio(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        service = RuntimeSnapshotService(session=deps.session, local_only=True)
        snapshot = service.capture(sections=frozenset({"portfolio"}))
        _print_portfolio(snapshot)
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_experiments(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        svc = ExperimentCatalogService(session=deps.session)
        rows = svc.list_experiments()[: args.limit]
        print(f"\nEXPERIMENTS (recent {len(rows)})")
        if not rows:
            print("  (none)")
        else:
            status_counts: dict[str, int] = {}
            for r in rows:
                status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
            summary = "  |  ".join(f"{s}: {c}" for s, c in sorted(status_counts.items()))
            print(f"  Summary:  {summary}")
            print()
            for r in rows:
                created = r["created_at"].strftime("%Y-%m-%d %H:%M")
                strats = (
                    f"{r['strategies_passed_filters']}/{r['total_strategies']} passed"
                    if r["total_strategies"]
                    else "no runs"
                )
                print(f"  {r['status']:<12}  {created}  {r['experiment_name']:<50}  {strats}")
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_runtime_jobs(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        svc = OperationsService(session=deps.session)
        jobs = svc.list_jobs()[: args.limit]
        print(f"\nRUNTIME JOBS ({len(jobs)})")
        if not jobs:
            print("  (none)")
        else:
            for j in jobs:
                last_started = (
                    j["last_started_at"].strftime("%Y-%m-%d %H:%M")
                    if j["last_started_at"]
                    else "never"
                )
                dur = f"{j['last_duration_ms']}ms" if j["last_duration_ms"] else "-"
                err = f"  ERR: {j['last_error_message']}" if j["last_error_message"] else ""
                print(
                    f"  {j['job_name']:<35}  {j['latest_status']:<12}  "
                    f"last={last_started}  dur={dur}  runs={j['run_count']}{err}"
                )
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


def handle_recent_errors(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        repo = RunManifestRepository(deps.session)
        rows = repo.list_failed_runs(limit=args.limit)
        print(f"\nRECENT ERRORS ({len(rows)} failed runs)")
        if not rows:
            print("  (none)")
        else:
            for row in rows:
                created = row.created_at.strftime("%Y-%m-%d %H:%M")
                strat = row.strategy_id or "-"
                step = row.current_step or "-"
                err = (row.error_message or "")[:80]
                print(
                    f"  {created}  {str(row.run_id)[:8]}  strat={strat:<25}  "
                    f"step={step:<20}  err={err}"
                )
        print()
        return 0
    except (OperationalError, ProgrammingError) as exc:
        return _db_not_ready(exc)
    finally:
        deps.session.close()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _pct(value: Decimal) -> str:
    return f"{float(value) * 100:+.2f}%"


def _write_snapshot_file(snapshot: RuntimeSnapshot, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Snapshot written to: {out.resolve()}")


def _print_portfolio(snap: RuntimeSnapshot) -> None:
    pf = snap.portfolio
    print("\nPORTFOLIO")
    if pf is None:
        print("  N/A")
        return

    s = pf.summary
    if s is None:
        print("  Summary:  N/A")
    else:
        total = float(s.current_portfolio_value)
        invested = float(s.invested_capital)
        cash = float(s.cash_balance)
        pct_deployed = (invested / total * 100) if total else 0.0
        pct_cash = (cash / total * 100) if total else 0.0
        today_sign = "+" if s.todays_pnl_amount >= 0 else "-"
        total_pnl_sign = "+" if s.total_pnl_amount >= 0 else "-"

        print(
            f"  Total Value:      ${total:>14,.2f}    {today_sign}${abs(float(s.todays_pnl_amount)):,.2f} today ({_pct(s.todays_pnl_percent)})"
        )
        print(f"  Invested Capital: ${invested:>14,.2f}    {pct_deployed:.1f}% deployed")
        print(f"  Cash Reserve:     ${cash:>14,.2f}    {pct_cash:.1f}% available")
        print(f"  Open Positions:   {s.open_positions}")
        print(
            f"  Total PnL:        {total_pnl_sign}${abs(float(s.total_pnl_amount)):,.2f}  ({_pct(s.total_pnl_percent)} all-time)"
        )

    print(f"\n  HOLDINGS ({len(pf.holdings)})")
    if not pf.holdings:
        print("    (none)")
    else:
        for h in pf.holdings:
            pnl = float(h.unrealized_pnl)
            pnl_str = f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}"
            print(
                f"    {h.symbol:<6}  qty={float(h.quantity):.4g}"
                f"  avg=${float(h.average_entry_price):.2f}"
                f"  cur=${float(h.current_price):.2f}"
                f"  val=${float(h.market_value):,.2f}"
                f"  pnl={pnl_str}"
                f"  [{h.strategy_id}]"
            )

    print(f"\n  ALLOCATION BY STRATEGY ({len(pf.allocation_by_strategy)})")
    if not pf.allocation_by_strategy:
        print("    (none)")
    else:
        for item in pf.allocation_by_strategy:
            bar_width = max(1, int(float(item.percent_of_portfolio) / 2))
            bar = "#" * bar_width
            print(
                f"    {item.name:<30}  {float(item.percent_of_portfolio):5.1f}%  {bar}  ${float(item.allocated_capital):,.0f}"
            )

    print("\n  PERFORMANCE")
    perf = pf.performance
    if perf is None:
        print("    N/A")
    else:
        print(f"    Total Return:   {_pct(perf.total_return)}")
        print(f"    Sharpe:         {float(perf.sharpe_ratio):.2f}")
        print(f"    Sortino:        {float(perf.sortino_ratio):.2f}")
        print(f"    Max Drawdown:   {_pct(perf.max_drawdown)}")
        print(f"    Volatility:     {_pct(perf.volatility)}")
        if perf.by_period:
            by_period_str = "  ".join(
                f"{p.period}: {_pct(p.return_percent)}" for p in perf.by_period
            )
            print(f"    By Period:      {by_period_str}")

    print("\n  RISK METRICS")
    risk = pf.risk
    if risk is None:
        print("    N/A")
    else:
        print(f"    Volatility (ann):  {_pct(risk.portfolio_volatility)}")
        print(f"    Beta (vs SPY):     {float(risk.beta):.2f}")
        print(f"    VaR 95% (1d):      ${float(risk.value_at_risk_1d_95):,.2f}")
        print(f"    Current Drawdown:  {_pct(risk.current_drawdown)}")
        print(f"    Avg Correlation:   {float(risk.average_pairwise_correlation):.3f}")


def _print_controls_section(snap: RuntimeSnapshot) -> None:
    print("\nOPERATOR CONTROLS")
    if snap.operator_controls is None:
        print("  N/A")
    else:
        c = snap.operator_controls
        if c.kill_switch_active:
            status = "HALTED  <- kill switch active"
        elif c.trading_paused:
            status = "PAUSED  <- soft pause active"
        elif not c.trading_enabled:
            status = "DISABLED  <- trading_enabled=false"
        else:
            status = "Active"
        print(f"  Trading Status:  {status}")
        print(f"  Kill Switch:     {_yn(c.kill_switch_active)}")
        print(f"  Trading Paused:  {_yn(c.trading_paused)}")
        print(f"  Trading Enabled: {_yn(c.trading_enabled)}")
        print(f"  Trading Mode:    {c.trading_mode}")
    print()


def _print_settings_section(snap: RuntimeSnapshot) -> None:
    print("\nOPERATOR SETTINGS")
    if snap.operator_settings is None:
        print("  N/A")
    else:
        s = snap.operator_settings
        feature_ver = next(
            (d.version_id for d in snap.datasets if d.dataset_name == "feature_version"),
            None,
        )
        print(f"  Max Portfolio DD:    {s.max_portfolio_drawdown * 100:.1f}%")
        print(f"  Max Strategy DD:     {s.max_strategy_drawdown * 100:.1f}%")
        print(f"  Risk Tolerance:      {s.risk_tolerance}")
        print(f"  Per-Strategy Cap:    {s.per_strategy_cap * 100:.1f}%")
        print(f"  Target Vol:          {s.target_portfolio_volatility * 100:.1f}%")
        print(f"  Min Sharpe:          {s.min_sharpe_for_promotion:.2f}")
        print(f"  Min Paper Days:      {s.min_paper_trading_period_days}")
        print(f"  Rebalance:           {s.rebalance_frequency}")
        print(f"  Slippage Model:      {s.slippage_model}")
        print(f"  Transaction Cost:    {s.transaction_cost_model}")
        print(f"  Feature Version:     {feature_ver or 'N/A'}")
        print(f"  Auto-Promote:        {_yn(s.auto_promote_enabled)}")
        print(f"  Auto-Demote:         {_yn(s.auto_demote_on_breach)}")
        print(f"  Drawdown Alerts:     {_yn(s.notify_drawdown_alerts)}")
        print(f"  Promotion Alerts:    {_yn(s.notify_strategy_promotion_events)}")
        print(f"  Pipeline Alerts:     {_yn(s.notify_pipeline_failures)}")
    print()


def _print_allocations_section(snap: RuntimeSnapshot) -> None:
    print(f"\nPORTFOLIO ALLOCATIONS ({len(snap.strategy_allocations)})")
    if not snap.strategy_allocations:
        print("  (none)")
    else:
        total_capital: Decimal | None = None
        for entry in snap.strategy_allocations:
            if entry.total_portfolio_capital is not None:
                total_capital = entry.total_portfolio_capital
            if entry.override_active and entry.override_amount is not None:
                amount_str = f"${entry.override_amount:,.0f}"
                reason_str = f"  ({entry.override_reason})" if entry.override_reason else ""
                print(f"  {entry.strategy_id:<30}  {amount_str}  OVERRIDE{reason_str}")
            else:
                print(f"  {entry.strategy_id:<30}  (policy)")
        print("  ---")
        if total_capital is not None:
            print(f"  Total Capital:  ${total_capital:,.0f}")
    print()


def _print_datasets_section(snap: RuntimeSnapshot) -> None:
    print("\nDATASETS")
    if not snap.datasets:
        print("  (none)")
    else:
        for entry in snap.datasets:
            ver = entry.version_id if entry.version_id else "N/A"
            print(f"  {entry.dataset_name:<18}  {ver}")
    print()


def _print_experiments_section(snap: RuntimeSnapshot) -> None:
    n_exp = len(snap.experiments)
    print(f"\nEXPERIMENTS (recent {n_exp})")
    if not snap.experiments:
        print("  (none)")
    else:
        status_counts: dict[str, int] = {}
        for exp in snap.experiments:
            status_counts[exp.status] = status_counts.get(exp.status, 0) + 1
        summary = "  | ".join(f"{s}: {c}" for s, c in sorted(status_counts.items()))
        print(f"  Summary:  {summary}")
        print()
        for exp in snap.experiments:
            created = exp.created_at.strftime("%Y-%m-%d %H:%M")
            strats = (
                f"{exp.strategies_passed_filters}/{exp.total_strategies} passed"
                if exp.total_strategies
                else "no runs"
            )
            print(f"  {exp.status:<12}  {created}  {exp.experiment_name:<50}  {strats}")
    print()


def _print_activity_section(snap: RuntimeSnapshot) -> None:
    n_activity = len(snap.recent_activity)
    print(f"\nRECENT ACTIVITY (last {n_activity})")
    if not snap.recent_activity:
        print("  (none)")
    else:
        for event in snap.recent_activity:
            time_str = event.timestamp.strftime("%H:%M:%S")
            actor = event.actor or "system"
            details = event.details or ""
            print(f"  {time_str}  {event.action_type:<40}  {actor:<20}  {details}")
    print()


def _print_snapshot(snap: RuntimeSnapshot) -> None:
    ts = snap.snapshot_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    print("\n=== RUNTIME SNAPSHOT ===")
    print(f"Captured: {ts}")

    _print_controls_section(snap)

    _print_settings_section(snap)

    _print_portfolio(snap)

    n = len(snap.strategy_controls)
    print(f"\nSTRATEGY CONTROLS ({n})")
    if not snap.strategy_controls:
        print("  (none)")
    else:
        for entry in snap.strategy_controls:
            status = "enabled" if entry.enabled else "DISABLED"
            suffix = f"  ({entry.pause_reason})" if entry.pause_reason else ""
            print(f"  {entry.strategy_id:<30}  {status}{suffix}")

    _print_allocations_section(snap)

    _print_datasets_section(snap)

    _print_experiments_section(snap)

    _print_activity_section(snap)
