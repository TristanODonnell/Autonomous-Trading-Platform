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


def _pct(value: Decimal) -> str:
    return f"{float(value) * 100:+.2f}%"


def _print_portfolio(snap: RuntimeSnapshot) -> None:
    pf = snap.portfolio
    print("\nPORTFOLIO")
    if pf is None:
        print("  N/A")
        return

    # --- Summary ---
    s = pf.summary
    if s is None:
        print("  Summary:  N/A")
    else:
        total = float(s.current_portfolio_value)
        invested = float(s.invested_capital)
        cash = float(s.cash_balance)
        pct_deployed = (invested / total * 100) if total else 0.0
        pct_cash = (cash / total * 100) if total else 0.0
        today_sign = "▲" if s.todays_pnl_amount >= 0 else "▼"
        total_pnl_sign = "▲" if s.total_pnl_amount >= 0 else "▼"

        print(
            f"  Total Value:      ${total:>14,.2f}    {today_sign} ${abs(float(s.todays_pnl_amount)):,.2f} today ({_pct(s.todays_pnl_percent)})"
        )
        print(f"  Invested Capital: ${invested:>14,.2f}    {pct_deployed:.1f}% deployed")
        print(f"  Cash Reserve:     ${cash:>14,.2f}    {pct_cash:.1f}% available")
        print(f"  Open Positions:   {s.open_positions}")
        print(
            f"  Total PnL:        {total_pnl_sign} ${abs(float(s.total_pnl_amount)):,.2f}  ({_pct(s.total_pnl_percent)} all-time)"
        )

    # --- Holdings ---
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

    # --- Allocation by Strategy ---
    print(f"\n  ALLOCATION BY STRATEGY ({len(pf.allocation_by_strategy)})")
    if not pf.allocation_by_strategy:
        print("    (none)")
    else:
        for item in pf.allocation_by_strategy:
            bar_width = max(1, int(float(item.percent_of_portfolio) / 2))
            bar = "█" * bar_width
            print(
                f"    {item.name:<30}  {float(item.percent_of_portfolio):5.1f}%  {bar}  ${float(item.allocated_capital):,.0f}"
            )

    # --- Performance ---
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

    # --- Risk ---
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

        # Derive a single human-readable status from the three independent flags.
        # kill_switch and trading_paused are set by separate API calls;
        # either alone is sufficient to block order submission.
        if c.kill_switch_active:
            trading_status = "HALTED  ← kill switch active"
        elif c.trading_paused:
            trading_status = "PAUSED  ← soft pause active"
        elif not c.trading_enabled:
            trading_status = "DISABLED  ← trading_enabled=false"
        else:
            trading_status = "Active"

        print(f"  Trading Status:  {trading_status}")
        print(f"  Kill Switch:     {_yn(c.kill_switch_active)}")
        print(
            f"  Trading Paused:  {_yn(c.trading_paused)}  (set via POST /controls/pause, independent of kill switch)"
        )
        print(f"  Trading Enabled: {_yn(c.trading_enabled)}")
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

    # --- Portfolio ---
    _print_portfolio(snap)

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

    # --- Experiments ---
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
