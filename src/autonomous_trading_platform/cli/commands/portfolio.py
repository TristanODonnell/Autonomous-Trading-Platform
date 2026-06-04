from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import yaml
from sqlalchemy import desc, select

from autonomous_trading_platform.application.services.portfolio_analytics_service import (
    PortfolioAnalyticsService,
)
from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_json
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.simulation_allocation_provider import (
    AllocationConfig,
    SimulationAllocationProvider,
    snapshot_allocation_config,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.factor_exposure_snapshot_repository import (
    FactorExposureSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.factor_neutralization_repository import (
    FactorNeutralizationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.position_snapshot_repository import (
    PositionSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.portfolio_construction_repository import (
    PortfolioConstructionRepository,
)

_ACTIVE_ALLOCATION_STATES = {
    "approved_for_paper_trading",
    "approved_for_live_trading",
}


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "portfolio",
        help="Inspect local portfolio state and construction artifacts.",
    )
    portfolio_subparsers = parser.add_subparsers(dest="portfolio_command", required=True)

    snapshot_parser = portfolio_subparsers.add_parser("snapshot", help="Show portfolio snapshot.")
    snapshot_parser.add_argument("--json", action="store_true")
    snapshot_parser.set_defaults(func=handle_snapshot)

    summary_parser = portfolio_subparsers.add_parser("summary", help="Show portfolio summary.")
    summary_parser.add_argument("--json", action="store_true")
    summary_parser.add_argument("--broker", action="store_true")
    summary_parser.add_argument("--account-id")
    summary_parser.set_defaults(func=handle_summary)

    holdings_parser = portfolio_subparsers.add_parser("holdings", help="Show current holdings.")
    holdings_parser.add_argument("--json", action="store_true")
    holdings_parser.add_argument("--broker", action="store_true")
    holdings_parser.add_argument("--account-id")
    holdings_parser.set_defaults(func=handle_holdings)

    allocation_parser = portfolio_subparsers.add_parser(
        "allocation",
        help="Show or resolve portfolio allocation.",
    )
    allocation_parser.add_argument("--json", action="store_true")
    allocation_parser.add_argument("--broker", action="store_true")
    allocation_parser.add_argument("--account-id")
    allocation_parser.set_defaults(func=handle_allocation)
    allocation_subparsers = allocation_parser.add_subparsers(dest="allocation_command")
    allocation_resolve = allocation_subparsers.add_parser(
        "resolve",
        help="Resolve live allocation for one strategy.",
    )
    allocation_resolve.add_argument("--strategy-id", required=True)
    allocation_resolve.add_argument("--state", required=True, type=_parse_governance_state)
    allocation_resolve.add_argument("--performance-tier")
    allocation_resolve.add_argument("--json", action="store_true")
    allocation_resolve.set_defaults(func=handle_allocation_resolve)
    allocation_many = allocation_subparsers.add_parser(
        "resolve-many",
        help="Resolve allocations for active strategies.",
    )
    allocation_many.add_argument("--state")
    allocation_many.add_argument("--json", action="store_true")
    allocation_many.set_defaults(func=handle_allocation_resolve_many)
    allocation_aggregate = allocation_subparsers.add_parser(
        "aggregate",
        help="Preview aggregate allocation utilization.",
    )
    allocation_aggregate.add_argument(
        "--proposed",
        action="append",
        default=[],
        help="strategy_id=allocation_pct; repeatable.",
    )
    allocation_aggregate.set_defaults(func=handle_allocation_aggregate)

    allocation_config = portfolio_subparsers.add_parser(
        "allocation-config",
        help="Snapshot and replay allocation configuration.",
    )
    allocation_config_subparsers = allocation_config.add_subparsers(
        dest="allocation_config_command",
        required=True,
    )
    allocation_config_show = allocation_config_subparsers.add_parser(
        "show",
        help="Print allocation config snapshot and hash.",
    )
    allocation_config_show.add_argument("--json", action="store_true")
    allocation_config_show.add_argument("--strategy-id", action="append", dest="strategy_ids")
    allocation_config_show.set_defaults(func=handle_allocation_config_show)
    allocation_config_snapshot = allocation_config_subparsers.add_parser(
        "snapshot",
        help="Write allocation config artifact.",
    )
    allocation_config_snapshot.add_argument("--output", required=True, type=Path)
    allocation_config_snapshot.add_argument("--strategy-id", action="append", dest="strategy_ids")
    allocation_config_snapshot.set_defaults(func=handle_allocation_config_snapshot)
    allocation_config_verify = allocation_config_subparsers.add_parser(
        "verify",
        help="Verify deterministic allocation from an artifact.",
    )
    allocation_config_verify.add_argument("--config", required=True, type=Path)
    allocation_config_verify.add_argument("--strategy-id", required=True)
    allocation_config_verify.add_argument("--state", required=True, type=_parse_governance_state)
    allocation_config_verify.add_argument("--performance-tier")
    allocation_config_verify.set_defaults(func=handle_allocation_config_verify)

    equity_curve = portfolio_subparsers.add_parser("equity-curve", help="Show equity curve.")
    equity_curve.add_argument("--period", default="1m")
    equity_curve.add_argument("--json", action="store_true")
    equity_curve.set_defaults(func=handle_equity_curve)

    performance = portfolio_subparsers.add_parser("performance", help="Show performance metrics.")
    performance.add_argument("--from-date", type=_parse_date)
    performance.add_argument("--to-date", type=_parse_date)
    performance.add_argument("--json", action="store_true")
    performance.set_defaults(func=handle_performance)

    period_perf = portfolio_subparsers.add_parser(
        "performance-by-period",
        help="Show dashboard returns by period.",
    )
    period_perf.add_argument("--json", action="store_true")
    period_perf.set_defaults(func=handle_performance_by_period)

    risk = portfolio_subparsers.add_parser("risk", help="Show dashboard portfolio risk.")
    risk.add_argument("--json", action="store_true")
    risk.set_defaults(func=handle_risk)

    cash = portfolio_subparsers.add_parser("cash", help="Inspect cash snapshots.")
    cash_subparsers = cash.add_subparsers(dest="cash_command", required=True)
    cash_latest = cash_subparsers.add_parser("latest", help="Show latest cash snapshot.")
    cash_latest.add_argument("--json", action="store_true")
    cash_latest.set_defaults(func=handle_cash_latest)

    positions = portfolio_subparsers.add_parser("positions", help="Inspect position snapshots.")
    positions_subparsers = positions.add_subparsers(dest="positions_command", required=True)
    positions_latest = positions_subparsers.add_parser(
        "latest",
        help="Show latest position snapshot.",
    )
    positions_latest.add_argument("--json", action="store_true")
    positions_latest.set_defaults(func=handle_positions_latest)

    reconcile = portfolio_subparsers.add_parser(
        "reconcile",
        help="Check summary, holdings, cash, and positions consistency.",
    )
    reconcile.add_argument("--json", action="store_true")
    reconcile.set_defaults(func=handle_reconcile)

    construction = portfolio_subparsers.add_parser(
        "construction",
        help="Inspect portfolio construction artifacts.",
    )
    construction_subparsers = construction.add_subparsers(
        dest="construction_command",
        required=True,
    )
    construction_runs = construction_subparsers.add_parser("runs", help="List runs.")
    construction_runs.add_argument("--limit", type=_positive_int, default=20)
    construction_runs.add_argument("--json", action="store_true")
    construction_runs.set_defaults(func=handle_construction_runs)
    construction_show = construction_subparsers.add_parser("show", help="Show run by batch ID.")
    construction_show.add_argument("--batch-id", required=True)
    construction_show.set_defaults(func=handle_construction_show)
    construction_by_run = construction_subparsers.add_parser(
        "by-run-id",
        help="Show latest run by runtime run ID.",
    )
    construction_by_run.add_argument("--run-id", required=True)
    construction_by_run.set_defaults(func=handle_construction_by_run_id)
    construction_raw = construction_subparsers.add_parser(
        "raw-signals",
        help="List raw strategy signals.",
    )
    construction_raw.add_argument("--batch-id", required=True)
    construction_raw.set_defaults(func=handle_construction_raw_signals)
    construction_netted = construction_subparsers.add_parser(
        "netted",
        help="List netted portfolio signals.",
    )
    construction_netted.add_argument("--batch-id", required=True)
    construction_netted.set_defaults(func=handle_construction_netted)
    construction_intents = construction_subparsers.add_parser(
        "intents",
        help="List constraint-gated intents.",
    )
    construction_intents.add_argument("--batch-id", required=True)
    construction_intents.add_argument("--constraint-status")
    construction_intents.set_defaults(func=handle_construction_intents)
    construction_conflicts = construction_subparsers.add_parser(
        "conflicts",
        help="List signal conflicts.",
    )
    construction_conflicts.add_argument("--batch-id", required=True)
    construction_conflicts.set_defaults(func=handle_construction_conflicts)
    construction_verify = construction_subparsers.add_parser(
        "verify",
        help="Verify diagnostics counts against artifact rows.",
    )
    construction_verify.add_argument("--batch-id", required=True)
    construction_verify.set_defaults(func=handle_construction_verify)

    factor_exposures = portfolio_subparsers.add_parser(
        "factor-exposures",
        help="Inspect portfolio-facing factor exposure snapshots.",
    )
    factor_exposure_subparsers = factor_exposures.add_subparsers(
        dest="factor_exposures_command",
        required=True,
    )
    factor_current = factor_exposure_subparsers.add_parser("current")
    factor_current.add_argument("--portfolio-id", default="paper")
    factor_current.add_argument("--lookback-window", type=int, default=20)
    factor_current.set_defaults(func=handle_factor_exposures_current)

    factor_neutralization = portfolio_subparsers.add_parser(
        "factor-neutralization",
        help="Inspect factor neutralization evidence.",
    )
    factor_neutralization_subparsers = factor_neutralization.add_subparsers(
        dest="factor_neutralization_command",
        required=True,
    )
    factor_neutral_current = factor_neutralization_subparsers.add_parser("current")
    factor_neutral_current.add_argument("--portfolio-id", default="paper")
    factor_neutral_current.set_defaults(func=handle_factor_neutralization_current)

    export = portfolio_subparsers.add_parser("export", help="Write portfolio artifact.")
    export.add_argument("--output", required=True, type=Path)
    export.set_defaults(func=handle_export)

    verify_dashboard = portfolio_subparsers.add_parser(
        "verify-dashboard-state",
        help="Verify local dashboard portfolio state.",
    )
    verify_dashboard.add_argument("--run-id", required=True)
    verify_dashboard.add_argument("--json", action="store_true")
    verify_dashboard.set_defaults(func=handle_verify_dashboard_state)

    simulate_allocation = portfolio_subparsers.add_parser(
        "simulate-allocation",
        help="Resolve allocation from a frozen config.",
    )
    simulate_allocation.add_argument("--config", required=True, type=Path)
    simulate_allocation.add_argument("--strategy-id", required=True)
    simulate_allocation.add_argument("--state", required=True, type=_parse_governance_state)
    simulate_allocation.add_argument("--performance-tier")
    simulate_allocation.add_argument("--capital", type=float)
    simulate_allocation.set_defaults(func=handle_simulate_allocation)


def handle_snapshot(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        print_json(_portfolio_snapshot(session))
        return 0
    finally:
        session.close()


def handle_summary(args: argparse.Namespace) -> int:
    if _broker_requested(args):
        return _broker_not_available("summary")
    session = get_session()
    try:
        print_json({"summary": PortfolioSummaryService(session=session).get_summary()})
        return 0
    finally:
        session.close()


def handle_holdings(args: argparse.Namespace) -> int:
    if _broker_requested(args):
        return _broker_not_available("holdings")
    session = get_session()
    try:
        print_json(PortfolioAnalyticsService(session=session).get_holdings())
        return 0
    finally:
        session.close()


def handle_allocation(args: argparse.Namespace) -> int:
    if _broker_requested(args):
        return _broker_not_available("allocation")
    session = get_session()
    try:
        print_json(_allocation_payload(session))
        return 0
    finally:
        session.close()


def handle_allocation_resolve(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        result = StrategyAllocationService(session=session, read_only=True).resolve_allocation(
            strategy_id=args.strategy_id,
            approval_status=args.state,
            performance_tier=args.performance_tier,
        )
        session.rollback()
        print_json({"allocation": result.model_dump(mode="json")})
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Allocation resolution failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_resolve_many(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = StrategyAllocationService(
            session=session,
            read_only=True,
        ).get_allocations_for_active_strategies()
        session.rollback()
        print_json({"strategies": rows, "source": "active_strategy_allocation_view"})
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Allocation list failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_aggregate(args: argparse.Namespace) -> int:
    proposed = _parse_proposed_overrides(args.proposed)
    if proposed is None:
        return 1
    session = get_session()
    try:
        service = StrategyAllocationService(session=session, read_only=True)
        if proposed:
            result = service.project_aggregate_allocation(proposed)
            payload = dict(result.__dict__)
        else:
            payload = service.get_aggregate_allocation_status()
        session.rollback()
        print_json(payload)
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Allocation aggregate preview failed: {exc}")
        return 1
    finally:
        session.close()


def handle_allocation_config_show(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        cfg = _snapshot_allocation_config(session, strategy_ids=args.strategy_ids)
        print_json({"allocation_config": cfg.to_artifact_dict()})
        return 0
    finally:
        session.close()


def handle_allocation_config_snapshot(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        cfg = _snapshot_allocation_config(session, strategy_ids=args.strategy_ids)
        payload = cfg.to_artifact_dict()
        _write_payload(args.output, payload)
        print_json(
            {
                "wrote": str(args.output),
                "allocation_config_hash": payload["allocation_config_hash"],
                "strategy_count": payload["strategy_count"],
            }
        )
        return 0
    finally:
        session.close()


def handle_allocation_config_verify(args: argparse.Namespace) -> int:
    cfg = _read_allocation_config(args.config)
    if cfg is None:
        return 1
    result = SimulationAllocationProvider(cfg).get_allocation(
        args.strategy_id,
        args.state,
        args.performance_tier,
    )
    print_json(
        {
            "verified": True,
            "no_db_calls_after_config_load": True,
            "allocation_config_hash": cfg.allocation_config_hash,
            "allocation": result.model_dump(mode="json"),
        }
    )
    return 0


def handle_equity_curve(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        print_json(PortfolioEquityCurveService(session=session).get_equity_curve(args.period))
        return 0
    finally:
        session.close()


def handle_performance(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        result = PortfolioAnalyticsService(session=session).get_performance(
            from_date=args.from_date,
            to_date=args.to_date,
        )
        print_json({"performance": result})
        return 0
    finally:
        session.close()


def handle_performance_by_period(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        print_json(PortfolioAnalyticsService(session=session).get_performance_by_period())
        return 0
    finally:
        session.close()


def handle_risk(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        print_json(
            {
                "risk": PortfolioAnalyticsService(session=session).get_risk(),
                "metadata": {
                    "scope": "portfolio_dashboard_risk",
                    "enforcement_domain": "risk",
                    "beta_source": "dashboard_placeholder",
                    "average_pairwise_correlation_source": "dashboard_placeholder",
                },
            }
        )
        return 0
    finally:
        session.close()


def handle_cash_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = CashSnapshotRepository(session).get_latest()
        print_json({"found": row is not None, "cash_snapshot": _cash_snapshot_payload(row)})
        return 0
    finally:
        session.close()


def handle_positions_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = PositionSnapshotRepository(session).get_latest()
        print_json(
            {
                "found": row is not None,
                "position_snapshot": _position_snapshot_payload(session, row),
            }
        )
        return 0
    finally:
        session.close()


def handle_reconcile(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        payload = _reconciliation_payload(session)
        print_json(payload)
        return 0 if payload["reconciled"] else 1
    finally:
        session.close()


def handle_construction_runs(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = PortfolioConstructionRepository(session=session).list_runs(limit=args.limit)
        print_json({"runs": [_run_row_to_dict(row) for row in rows], "limit": args.limit})
        return 0
    finally:
        session.close()


def handle_construction_show(args: argparse.Namespace) -> int:
    return _print_construction_run(batch_id=args.batch_id)


def handle_construction_by_run_id(args: argparse.Namespace) -> int:
    return _print_construction_run(run_id=args.run_id)


def handle_construction_raw_signals(args: argparse.Namespace) -> int:
    return _print_construction_rows(
        args.batch_id,
        "raw_signals",
        lambda repo, batch_id: [
            _batch_item_to_dict(row) for row in repo.list_raw_signals(batch_id)
        ],
    )


def handle_construction_netted(args: argparse.Namespace) -> int:
    return _print_construction_rows(
        args.batch_id,
        "netted_signals",
        lambda repo, batch_id: [
            _netted_signal_to_dict(row) for row in repo.list_netted_signals(batch_id)
        ],
    )


def handle_construction_intents(args: argparse.Namespace) -> int:
    def _rows(repo, batch_id):
        rows = repo.list_signal_intents(batch_id)
        if args.constraint_status is not None:
            rows = [row for row in rows if row.constraint_status == args.constraint_status]
        return [_signal_intent_to_dict(row) for row in rows]

    return _print_construction_rows(args.batch_id, "signal_intents", _rows)


def handle_construction_conflicts(args: argparse.Namespace) -> int:
    return _print_construction_rows(
        args.batch_id,
        "conflicts",
        lambda repo, batch_id: [
            _netted_signal_to_dict(row) for row in repo.list_conflicts(batch_id)
        ],
    )


def handle_construction_verify(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = PortfolioConstructionRepository(session=session)
        run = repo.get_run_by_batch(batch_id=args.batch_id)
        if run is None:
            print_error(f"No construction run found for batch_id={args.batch_id}")
            return 1
        raw_count = len(repo.list_raw_signals(args.batch_id))
        netted_count = len(repo.list_netted_signals(args.batch_id))
        intents = repo.list_signal_intents(args.batch_id)
        rejected_count = sum(1 for row in intents if row.constraint_status == "rejected")
        conflicts_count = len(repo.list_conflicts(args.batch_id))
        checks = {
            "raw_signal_count_matches": raw_count == run.raw_signal_count,
            "netted_signal_count_matches": netted_count == run.netted_signal_count,
            "constraint_rejected_count_matches": rejected_count == run.constraint_rejected_count,
            "conflict_count_matches": conflicts_count == run.conflict_count,
            "raw_signal_rows_present_when_expected": not (
                run.raw_signal_count > 0 and raw_count == 0
            ),
        }
        print_json(
            {
                "batch_id": args.batch_id,
                "verified": all(checks.values()),
                "checks": checks,
                "diagnostics_counts": {
                    "raw_signal_count": run.raw_signal_count,
                    "netted_signal_count": run.netted_signal_count,
                    "constraint_rejected_count": run.constraint_rejected_count,
                    "conflict_count": run.conflict_count,
                },
                "artifact_counts": {
                    "raw_signal_count": raw_count,
                    "netted_signal_count": netted_count,
                    "constraint_rejected_count": rejected_count,
                    "conflict_count": conflicts_count,
                },
            }
        )
        return 0 if all(checks.values()) else 1
    finally:
        session.close()


def handle_factor_exposures_current(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = FactorExposureSnapshotRepository(session).get_latest_snapshot(
            portfolio_id=args.portfolio_id,
            lookback_window=args.lookback_window,
        )
        print_json({"found": row is not None, "snapshot": _factor_snapshot_payload(row)})
        return 0
    finally:
        session.close()


def handle_factor_neutralization_current(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = FactorNeutralizationRepository(session).get_latest(portfolio_id=args.portfolio_id)
        print_json(
            {
                "found": row is not None,
                "factor_neutralization": _factor_neutralization_payload(row),
            }
        )
        return 0
    finally:
        session.close()


def handle_export(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        payload = _portfolio_snapshot(session)
        payload["exported_at"] = datetime.now(UTC).isoformat()
        payload["cash_snapshot"] = _cash_snapshot_payload(
            CashSnapshotRepository(session).get_latest()
        )
        payload["position_snapshot"] = _position_snapshot_payload(
            session,
            PositionSnapshotRepository(session).get_latest(),
        )
        payload["construction"] = {
            "recent_runs": [
                _run_row_to_dict(row)
                for row in PortfolioConstructionRepository(session=session).list_runs(limit=20)
            ]
        }
        _write_payload(args.output, payload)
        print_json({"wrote": str(args.output)})
        return 0
    finally:
        session.close()


def handle_verify_dashboard_state(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        cash = _latest_cash_for_run(session, args.run_id)
        position_snapshot = _latest_position_for_run(session, args.run_id)
        summary = PortfolioSummaryService(session=session).get_summary()
        curve = PortfolioEquityCurveService(session=session).get_equity_curve("1w")
        latest_curve_value = curve["points"][-1]["value"] if curve.get("points") else None
        checks = {
            "cash_snapshot_found_for_run": cash is not None,
            "position_snapshot_found_for_run": position_snapshot is not None,
            "summary_cash_matches_latest_run_cash": (
                cash is not None and Decimal(str(summary["cash_balance"])) == Decimal(cash.cash)
            ),
            "summary_equity_matches_latest_run_equity": (
                cash is not None
                and cash.equity is not None
                and Decimal(str(summary["current_portfolio_value"])) == Decimal(cash.equity)
            ),
            "equity_curve_latest_matches_latest_run_equity": (
                cash is not None
                and cash.equity is not None
                and latest_curve_value is not None
                and Decimal(str(latest_curve_value)) == Decimal(cash.equity)
            ),
        }
        print_json(
            {
                "run_id": args.run_id,
                "verified": all(checks.values()),
                "checks": checks,
                "latest_cash_snapshot": _cash_snapshot_payload(cash),
                "latest_position_snapshot": _position_snapshot_payload(session, position_snapshot),
                "portfolio_summary": summary,
                "equity_curve_latest_value": latest_curve_value,
            }
        )
        return 0 if all(checks.values()) else 1
    finally:
        session.close()


def handle_simulate_allocation(args: argparse.Namespace) -> int:
    cfg = _read_allocation_config(args.config)
    if cfg is None:
        return 1
    provider = SimulationAllocationProvider(cfg)
    if args.capital is not None:
        provider.update_total_capital(args.capital)
    result = provider.get_allocation(
        args.strategy_id,
        args.state,
        args.performance_tier,
    )
    print_json(
        {
            "allocation_config_hash": cfg.allocation_config_hash,
            "total_capital": provider.total_capital,
            "no_db_calls_after_config_load": True,
            "allocation": result.model_dump(mode="json"),
        }
    )
    return 0


def _portfolio_snapshot(session) -> dict[str, Any]:
    analytics = PortfolioAnalyticsService(session=session)
    return {
        "source": "local_db",
        "external_broker": False,
        "portfolio_summary": PortfolioSummaryService(session=session).get_summary(),
        "holdings": analytics.get_holdings(),
        "allocation": _allocation_payload(session),
        "risk": {
            "metrics": analytics.get_risk(),
            "metadata": {
                "scope": "portfolio_dashboard_risk",
                "enforcement_domain": "risk",
            },
        },
        "performance": analytics.get_performance(),
        "equity_curve_1m": PortfolioEquityCurveService(session=session).get_equity_curve("1m"),
    }


def _allocation_payload(session) -> dict[str, Any]:
    result = PortfolioAnalyticsService(session=session).get_allocation()
    source = "latest_position_snapshot"
    if not result.get("by_strategy"):
        result = _allocation_from_strategy_config(session)
        source = "active_strategy_config_fallback"
    return {
        **result,
        "metadata": {
            "source": source,
            "external_broker": False,
            "fallback_used": source == "active_strategy_config_fallback",
            "fallback_note": (
                "No live holdings found; by_strategy uses configured active strategy "
                "allocation percentages."
                if source == "active_strategy_config_fallback"
                else None
            ),
        },
    }


def _allocation_from_strategy_config(session) -> dict[str, Any]:
    rows = StrategyAllocationService(
        session=session,
        read_only=True,
    ).get_allocations_for_active_strategies()
    by_strategy = [
        {
            "name": row["display_name"],
            "strategy_id": row["strategy_id"],
            "allocated_capital": row["allocated_capital"] or Decimal("0"),
            "percent_of_portfolio": row["allocation_pct"] or Decimal("0"),
            "is_overridden": row["is_overridden"],
        }
        for row in rows
    ]
    return {"by_strategy": by_strategy, "by_asset": []}


def _snapshot_allocation_config(session, *, strategy_ids: list[str] | None) -> AllocationConfig:
    return snapshot_allocation_config(
        policies_repo=CapitalAllocationPoliciesRepository(session),
        overrides_repo=AllocationOverridesRepository(session),
        total_capital=_total_capital_for_snapshot(session),
        strategy_ids=strategy_ids or _active_strategy_ids(session),
        captured_by="portfolio_cli",
    )


def _total_capital_for_snapshot(session) -> float:
    latest_cash = CashSnapshotRepository(session).get_latest()
    if latest_cash is not None and latest_cash.equity is not None:
        return float(latest_cash.equity)
    return float(Settings().initial_capital)


def _active_strategy_ids(session) -> list[str]:
    rows = list(
        session.scalars(
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(_ACTIVE_ALLOCATION_STATES))
            .order_by(StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc())
        ).all()
    )
    seen: set[str] = set()
    strategy_ids: list[str] = []
    for row in rows:
        if row.strategy_id not in seen:
            seen.add(row.strategy_id)
            strategy_ids.append(row.strategy_id)
    return strategy_ids


def _reconciliation_payload(session) -> dict[str, Any]:
    summary = PortfolioSummaryService(session=session).get_summary()
    holdings = PortfolioAnalyticsService(session=session).get_holdings()["holdings"]
    cash = CashSnapshotRepository(session).get_latest()
    positions = PositionSnapshotRepository(session).get_latest()
    holdings_value = sum(
        (Decimal(str(row.get("market_value", 0))) for row in holdings),
        Decimal("0"),
    )
    expected_value = holdings_value + Decimal(str(summary["cash_balance"]))
    current_value = Decimal(str(summary["current_portfolio_value"]))
    checks = {
        "cash_snapshot_found": cash is not None,
        "position_snapshot_found": positions is not None,
        "holdings_plus_cash_matches_summary": expected_value == current_value,
    }
    return {
        "reconciled": all(checks.values()),
        "checks": checks,
        "current_portfolio_value": current_value,
        "holdings_market_value": holdings_value,
        "cash_balance": Decimal(str(summary["cash_balance"])),
        "expected_portfolio_value": expected_value,
        "latest_cash_snapshot_id": str(cash.snapshot_id) if cash is not None else None,
        "latest_position_snapshot_id": str(positions.snapshot_id)
        if positions is not None
        else None,
    }


def _print_construction_run(*, batch_id: str | None = None, run_id: str | None = None) -> int:
    session = get_session()
    try:
        repo = PortfolioConstructionRepository(session=session)
        row = (
            repo.get_run_by_batch(batch_id=batch_id)
            if batch_id
            else repo.get_run(run_id=run_id or "")
        )
        if row is None:
            label = f"batch_id={batch_id}" if batch_id else f"run_id={run_id}"
            print_error(f"No construction run found for {label}")
            return 1
        print_json({"run": _run_row_to_dict(row)})
        return 0
    finally:
        session.close()


def _print_construction_rows(batch_id: str, key: str, loader) -> int:
    session = get_session()
    try:
        repo = PortfolioConstructionRepository(session=session)
        print_json({"batch_id": batch_id, key: loader(repo, batch_id)})
        return 0
    finally:
        session.close()


def _latest_cash_for_run(session, run_id: str) -> CashSnapshot | None:
    return cast(
        CashSnapshot | None,
        session.scalars(
            select(CashSnapshot)
            .where(CashSnapshot.run_id == _run_id_value(run_id))
            .order_by(desc(CashSnapshot.timestamp), desc(CashSnapshot.snapshot_id))
            .limit(1)
        ).one_or_none(),
    )


def _latest_position_for_run(session, run_id: str) -> PositionSnapshot | None:
    return cast(
        PositionSnapshot | None,
        session.scalars(
            select(PositionSnapshot)
            .where(PositionSnapshot.run_id == _run_id_value(run_id))
            .order_by(desc(PositionSnapshot.timestamp), desc(PositionSnapshot.snapshot_id))
            .limit(1)
        ).one_or_none(),
    )


def _position_items(session, snapshot_id) -> list[PositionSnapshotItem]:
    return list(
        session.scalars(
            select(PositionSnapshotItem)
            .where(PositionSnapshotItem.snapshot_id == snapshot_id)
            .order_by(PositionSnapshotItem.symbol.asc())
        ).all()
    )


def _cash_snapshot_payload(row: CashSnapshot | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "snapshot_id": str(row.snapshot_id),
        "run_id": str(row.run_id),
        "timestamp": _iso(row.timestamp),
        "currency": row.currency,
        "cash": row.cash,
        "buying_power": row.buying_power,
        "reserved_cash": row.reserved_cash,
        "settled_cash": row.settled_cash,
        "unsettled_cash": row.unsettled_cash,
        "equity": row.equity,
        "source": _enum_value(row.source),
        "capital_bucket": row.capital_bucket,
        "freshness": {
            "age_seconds": (
                (datetime.now(UTC) - row.timestamp).total_seconds() if row.timestamp else None
            ),
            "as_of": _iso(row.timestamp),
        },
    }


def _position_snapshot_payload(session, row: PositionSnapshot | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "snapshot_id": str(row.snapshot_id),
        "run_id": str(row.run_id),
        "timestamp": _iso(row.timestamp),
        "source": _enum_value(row.source),
        "items": [
            _position_item_payload(item) for item in _position_items(session, row.snapshot_id)
        ],
    }


def _position_item_payload(row: PositionSnapshotItem) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "quantity": row.quantity,
        "avg_cost": row.avg_cost,
        "market_price": row.market_price,
        "market_value": row.market_value,
        "unrealized_pnl": row.unrealized_pnl,
    }


def _run_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "batch_id": row.batch_id,
        "bar_timestamp": _iso(row.bar_timestamp),
        "constructed_at": _iso(row.constructed_at),
        "netting_policy": row.netting_policy,
        "strategy_count": row.strategy_count,
        "symbol_count": row.symbol_count,
        "raw_signal_count": row.raw_signal_count,
        "netted_signal_count": row.netted_signal_count,
        "suppressed_count": row.suppressed_count,
        "suppressed_notional_usd": row.suppressed_notional_usd,
        "conflict_count": row.conflict_count,
        "constraint_applied_count": row.constraint_applied_count,
        "constraint_rejected_count": row.constraint_rejected_count,
        "raw_gross_signal_exposure": row.raw_gross_signal_exposure,
        "net_signal_exposure": row.net_signal_exposure,
        "exposure_before_constraints": row.exposure_before_constraints,
        "exposure_after_constraints": row.exposure_after_constraints,
        "final_order_count": row.final_order_count,
        "construction_duration_seconds": row.construction_duration_seconds,
    }


def _batch_item_to_dict(row: Any) -> dict[str, Any]:
    return {
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "signal_id": row.signal_id,
        "strategy_id": row.strategy_id,
        "symbol": row.symbol,
        "direction": row.direction,
        "confidence": row.confidence,
        "bar_timestamp": _iso(row.bar_timestamp),
        "strategy_version": row.strategy_version,
        "feature_snapshot_ref": row.feature_snapshot_ref,
    }


def _netted_signal_to_dict(row: Any) -> dict[str, Any]:
    return {
        "portfolio_signal_id": row.portfolio_signal_id,
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "symbol": row.symbol,
        "net_direction": row.net_direction,
        "net_confidence": row.net_confidence,
        "conflict_detected": row.conflict_detected,
        "constraint_status": row.constraint_status,
        "netting_policy": row.netting_policy,
        "gross_buy_notional": row.gross_buy_notional,
        "gross_sell_notional": row.gross_sell_notional,
        "net_notional": row.net_notional,
        "contributing_strategy_ids": row.contributing_strategy_ids,
        "attribution": row.attribution_json,
        "bar_timestamp": _iso(row.bar_timestamp),
    }


def _signal_intent_to_dict(row: Any) -> dict[str, Any]:
    return {
        "intent_id": row.intent_id,
        "portfolio_signal_id": row.portfolio_signal_id,
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "symbol": row.symbol,
        "direction": row.direction,
        "confidence": row.confidence,
        "constraint_status": row.constraint_status,
        "constraints_triggered": row.constraints_triggered,
        "attribution": row.attribution_json,
        "bar_timestamp": _iso(row.bar_timestamp),
    }


def _factor_snapshot_payload(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "snapshot_id": row.snapshot_id,
        "run_id": row.run_id,
        "portfolio_id": row.portfolio_id,
        "computed_at": _iso(row.computed_at),
        "as_of_date": _iso(row.as_of_date),
        "lookback_window": row.lookback_window,
        "benchmark_symbol": row.benchmark_symbol,
        "portfolio_exposures": row.portfolio_exposures,
        "strategy_exposures": row.strategy_exposures,
        "symbol_exposures": row.symbol_exposures,
        "sector_exposures": row.sector_exposures,
        "concentration_diagnostics": row.concentration_diagnostics,
        "warnings": row.warnings,
        "duration_seconds": row.duration_seconds,
    }


def _factor_neutralization_payload(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "run_id": row.run_id,
        "generated_at": _iso(row.generated_at),
        "status": row.status,
        "mode": row.mode,
        "portfolio_id": row.portfolio_id,
        "factor_snapshot_id": row.factor_snapshot_id,
        "optimization_run_id": row.optimization_run_id,
        "original_weights": row.original_weights,
        "target_weights": row.target_weights,
        "pre_exposures": row.pre_exposures,
        "post_exposures": row.post_exposures,
        "exposure_reduction": row.exposure_reduction,
        "binding_constraints": row.binding_constraints,
        "constraint_violations": row.constraint_violations,
        "fallback_mode": row.fallback_mode,
        "infeasibility_reason": row.infeasibility_reason,
        "duration_seconds": row.duration_seconds,
        "warnings": row.warnings,
        "metadata": row.metadata_json,
    }


def _read_allocation_config(path: Path) -> AllocationConfig | None:
    if not path.exists():
        print_error(f"Config file not found: {path}")
        return None
    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise ValueError("allocation config must be an object")
        return AllocationConfig.from_artifact_dict(raw.get("allocation_config", raw))
    except Exception as exc:
        print_error(f"Failed to read allocation config: {exc}")
        return None


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(_jsonable(payload), sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(_jsonable(payload), indent=2, default=str), encoding="utf-8")


def _parse_proposed_overrides(values: list[str]) -> dict[str, Decimal] | None:
    proposed: dict[str, Decimal] = {}
    for value in values:
        if "=" not in value:
            print_error(f"Invalid --proposed value: {value}. Use strategy_id=allocation_pct.")
            return None
        strategy_id, raw_pct = value.split("=", 1)
        try:
            pct = Decimal(raw_pct)
        except Exception:
            print_error(f"Invalid allocation pct for {strategy_id}: {raw_pct}")
            return None
        if pct < Decimal("0") or pct > Decimal("100"):
            print_error(f"Allocation pct for {strategy_id} must be between 0 and 100.")
            return None
        proposed[strategy_id] = pct
    return proposed


def _parse_governance_state(value: str) -> GovernanceState:
    try:
        return GovernanceState(value)
    except ValueError as exc:
        choices = ", ".join(state.value for state in GovernanceState)
        raise argparse.ArgumentTypeError(f"state must be one of: {choices}") from exc


def _parse_date(value: str):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _broker_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "broker", False) or getattr(args, "account_id", None))


def _broker_not_available(command: str) -> int:
    print_error(
        f"Broker-backed portfolio {command} is intentionally not wired in this CLI yet. "
        "Run without --broker for local DB-backed reads."
    )
    return 1


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _run_id_value(value: str) -> UUID | str:
    try:
        return UUID(value)
    except ValueError:
        return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return _enum_value(value)
