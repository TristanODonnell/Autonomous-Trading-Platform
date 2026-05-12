from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_snapshot_service import (
    RuntimeSnapshotService,
)
from autonomous_trading_platform.cli.formatters import print_json
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.runtime_snapshot import RuntimeSnapshot
from autonomous_trading_platform.db import get_session


@dataclass
class DiagnosticsCliDependencies:
    session: Session
    settings: Settings


def build_dependencies() -> DiagnosticsCliDependencies:
    return DiagnosticsCliDependencies(
        session=get_session(),
        settings=Settings(),
    )


def register(subparsers) -> None:
    diagnostics_parser = subparsers.add_parser("diagnostics", help="Diagnostics commands")
    diagnostics_subparsers = diagnostics_parser.add_subparsers(
        dest="diagnostics_command",
        required=True,
    )

    snapshot_parser = diagnostics_subparsers.add_parser(
        "snapshot",
        help="Print current runtime state for debugging",
    )
    snapshot_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON instead of human-readable text",
    )
    snapshot_parser.set_defaults(func=handle_snapshot)


def handle_snapshot(args: argparse.Namespace) -> int:
    deps = build_dependencies()
    try:
        service = RuntimeSnapshotService(session=deps.session)
        snapshot = service.capture()

        if args.as_json:
            print_json(snapshot.model_dump(mode="json"))
        else:
            _print_snapshot(snapshot)

        return 0
    finally:
        deps.session.close()


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _print_snapshot(snap: RuntimeSnapshot) -> None:
    ts = snap.snapshot_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    print("\n=== RUNTIME SNAPSHOT ===")
    print(f"Captured: {ts}")

    # --- Operator Controls ---
    print("\nOPERATOR CONTROLS")
    if snap.operator_controls is None:
        print("  N/A")
    else:
        c = snap.operator_controls
        print(f"  Trading Paused:  {_yn(c.global_trading_paused)}")
        print(f"  Kill Switch:     {_yn(c.kill_switch_active)}")
        print(f"  Trading Mode:    {c.trading_mode}")

    # --- Operator Settings ---
    print("\nOPERATOR SETTINGS")
    if snap.operator_settings is None:
        print("  N/A")
    else:
        s = snap.operator_settings

        # Resolve feature dataset version from the datasets list
        feature_ver = next(
            (d.version_id for d in snap.datasets if d.dataset_name == "feature_version"),
            None,
        )

        # Risk & sizing
        print(f"  Max Portfolio DD:    {s.max_portfolio_drawdown * 100:.1f}%")
        print(f"  Max Strategy DD:     {s.max_strategy_drawdown * 100:.1f}%")
        print(f"  Risk Tolerance:      {s.risk_tolerance}")
        print(f"  Per-Strategy Cap:    {s.per_strategy_cap * 100:.1f}%")
        print(f"  Target Vol:          {s.target_portfolio_volatility * 100:.1f}%")
        print(f"  Min Sharpe:          {s.min_sharpe_for_promotion:.2f}")
        print(f"  Min Paper Days:      {s.min_paper_trading_period_days}")
        print(f"  Rebalance:           {s.rebalance_frequency}")
        # Models
        print(f"  Slippage Model:      {s.slippage_model}")
        print(f"  Transaction Cost:    {s.transaction_cost_model}")
        print(f"  Feature Version:     {feature_ver or 'N/A'}")
        # Governance switches
        print(f"  Auto-Promote:        {_yn(s.auto_promote_enabled)}")
        print(f"  Auto-Demote:         {_yn(s.auto_demote_on_breach)}")
        # Alert flags
        print(f"  Drawdown Alerts:     {_yn(s.notify_drawdown_alerts)}")
        print(f"  Promotion Alerts:    {_yn(s.notify_strategy_promotion_events)}")
        print(f"  Pipeline Alerts:     {_yn(s.notify_pipeline_failures)}")

    # --- Strategy Controls ---
    n = len(snap.strategy_controls)
    print(f"\nSTRATEGY CONTROLS ({n})")
    if not snap.strategy_controls:
        print("  (none)")
    else:
        for entry in snap.strategy_controls:
            status = "enabled" if entry.enabled else "DISABLED"
            suffix = f"  ({entry.pause_reason})" if entry.pause_reason else ""
            print(f"  {entry.strategy_id:<30}  {status}{suffix}")

    # --- Portfolio Allocations ---
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
                print(f"  {entry.strategy_id:<30}  {amount_str}  ⚠ OVERRIDE{reason_str}")
            else:
                print(f"  {entry.strategy_id:<30}  (policy)")
        print("  ---")
        if total_capital is not None:
            print(f"  Total Capital:  ${total_capital:,.0f}")

    # --- Datasets ---
    print("\nDATASETS")
    if not snap.datasets:
        print("  (none)")
    else:
        for entry in snap.datasets:
            ver = entry.version_id if entry.version_id else "N/A"
            print(f"  {entry.dataset_name:<18}  {ver}")

    # --- Recent Activity ---
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
