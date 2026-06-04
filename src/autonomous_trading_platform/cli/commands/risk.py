from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from autonomous_trading_platform.application.services.drawdown_governance_service import (
    EVENT_BREACHED,
    EVENT_PROBATION,
    EVENT_RECOVERY,
    EVENT_SUSPENDED,
    EVENT_WARNING,
    DrawdownGovernanceService,
)
from autonomous_trading_platform.application.services.factor_exposure_monitoring_service import (
    FactorExposureMonitoringConfig,
    FactorExposureMonitoringService,
)
from autonomous_trading_platform.application.services.mean_variance_optimizer import (
    MeanVarianceConfig,
    MeanVarianceOptimizer,
)
from autonomous_trading_platform.application.services.risk_budgeting_service import (
    RiskBudgetConfig,
    RiskBudgetingService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_json
from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.runtime.optimizer import OptimizationObjective
from autonomous_trading_platform.contracts.runtime.risk_budget import AllocationMode
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)
from autonomous_trading_platform.storage.sor.models.risk_snapshots import (
    RiskSnapshot as RiskSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core import (
    drawdown_governance_ladder_transition_repository as ddg_transition_repository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
    DrawdownGovernanceLadderStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_snapshot_repository import (
    RiskSnapshotRepository,
)

_RISK_AUDIT_EVENT_TYPES = {
    "DRAWDOWN_GOVERNANCE_LADDER_RUN",
    "GOVERNANCE_DRAWDOWN_LADDER_TRANSITION",
    EVENT_WARNING,
    EVENT_PROBATION,
    EVENT_SUSPENDED,
    EVENT_BREACHED,
    EVENT_RECOVERY,
    "PORTFOLIO_DRAWDOWN_BREACH",
    "PORTFOLIO_DRAWDOWN_RECOVERY",
    "PORTFOLIO_SYMBOL_EXPOSURE_BLOCKED",
    "SECTOR_CONCENTRATION_LIMIT_BLOCKED",
}


def register(subparsers) -> None:
    parser = subparsers.add_parser("risk", help="Inspect and verify risk constraints.")
    risk_subparsers = parser.add_subparsers(dest="risk_command", required=True)

    limits_parser = risk_subparsers.add_parser("limits", help="Inspect active limits.")
    limits_subparsers = limits_parser.add_subparsers(dest="limits_command", required=True)
    limits_show = limits_subparsers.add_parser("show", help="Show active risk limits.")
    limits_show.add_argument("--json", action="store_true")
    limits_show.set_defaults(func=handle_limits_show)

    snapshot_parser = risk_subparsers.add_parser("snapshot", help="Risk snapshots.")
    snapshot_subparsers = snapshot_parser.add_subparsers(dest="snapshot_command", required=True)
    snapshot_latest = snapshot_subparsers.add_parser("latest", help="Show latest snapshot.")
    snapshot_latest.add_argument("--json", action="store_true")
    snapshot_latest.set_defaults(func=handle_snapshot_latest)
    snapshot_history = snapshot_subparsers.add_parser("history", help="List snapshots.")
    snapshot_history.add_argument("--limit", type=_positive_int, default=20)
    snapshot_history.set_defaults(func=handle_snapshot_history)
    snapshot_compute = snapshot_subparsers.add_parser("compute", help="Compute a snapshot.")
    snapshot_compute.add_argument("--run-id", required=True)
    snapshot_compute.add_argument("--as-of", required=True)
    group = snapshot_compute.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--persist", action="store_true")
    snapshot_compute.set_defaults(func=handle_snapshot_compute)

    exposure_parser = risk_subparsers.add_parser("exposure", help="Exposure reports.")
    exposure_subparsers = exposure_parser.add_subparsers(dest="exposure_command", required=True)
    exposure_current = exposure_subparsers.add_parser("current", help="Show exposure.")
    exposure_current.add_argument("--json", action="store_true")
    exposure_current.set_defaults(func=handle_exposure_current)

    concentration_parser = risk_subparsers.add_parser(
        "concentration", help="Concentration reports."
    )
    concentration_subparsers = concentration_parser.add_subparsers(
        dest="concentration_command", required=True
    )
    concentration_current = concentration_subparsers.add_parser(
        "current", help="Show concentration."
    )
    concentration_current.add_argument("--top", type=_positive_int, default=10)
    concentration_current.set_defaults(func=handle_concentration_current)

    pretrade_parser = risk_subparsers.add_parser(
        "pretrade", help="Evaluate hypothetical orders locally."
    )
    pretrade_subparsers = pretrade_parser.add_subparsers(dest="pretrade_command", required=True)
    pretrade_check = pretrade_subparsers.add_parser("check", help="Check one order.")
    pretrade_check.add_argument("--symbol", required=True)
    pretrade_check.add_argument("--side", required=True, type=_parse_side)
    pretrade_check.add_argument("--qty", required=True, type=Decimal)
    pretrade_check.add_argument("--limit-price", required=True, type=Decimal)
    pretrade_check.add_argument("--strategy-id", required=True)
    pretrade_check.add_argument("--json", action="store_true")
    pretrade_check.set_defaults(func=handle_pretrade_check)

    drawdown_parser = risk_subparsers.add_parser("drawdown", help="Drawdown ladder.")
    drawdown_subparsers = drawdown_parser.add_subparsers(dest="drawdown_command", required=True)
    drawdown_list = drawdown_subparsers.add_parser("list", help="List ladder states.")
    drawdown_list.add_argument("--state")
    drawdown_list.add_argument("--json", action="store_true")
    drawdown_list.set_defaults(func=handle_drawdown_list)
    drawdown_show = drawdown_subparsers.add_parser("show", help="Show one ladder state.")
    drawdown_show.add_argument("--strategy-id", required=True)
    drawdown_show.set_defaults(func=handle_drawdown_show)
    drawdown_transitions = drawdown_subparsers.add_parser(
        "transitions", help="Show ladder transition history."
    )
    drawdown_transitions.add_argument("--strategy-id", required=True)
    drawdown_transitions.add_argument("--limit", type=_positive_int, default=50)
    drawdown_transitions.set_defaults(func=handle_drawdown_transitions)
    drawdown_pending = drawdown_subparsers.add_parser(
        "pending-ack", help="List pending breach acknowledgements."
    )
    drawdown_pending.set_defaults(func=handle_drawdown_pending_ack)
    drawdown_ack = drawdown_subparsers.add_parser(
        "acknowledge", help="Acknowledge a breached ladder state."
    )
    drawdown_ack.add_argument("--strategy-id", required=True)
    drawdown_ack.add_argument("--operator", required=True)
    drawdown_ack.add_argument("--reason", required=True)
    drawdown_ack.set_defaults(func=handle_drawdown_acknowledge)
    drawdown_evaluate = drawdown_subparsers.add_parser("evaluate", help="Evaluate drawdown ladder.")
    group = drawdown_evaluate.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--persist", action="store_true")
    drawdown_evaluate.add_argument("--run-id")
    drawdown_evaluate.set_defaults(func=handle_drawdown_evaluate)

    budget_parser = risk_subparsers.add_parser("budget", help="Risk budgeting.")
    budget_subparsers = budget_parser.add_subparsers(dest="budget_command", required=True)
    budget_compute = budget_subparsers.add_parser("compute", help="Compute budget preview.")
    _add_budget_args(budget_compute)
    budget_compute.add_argument("--dry-run", action="store_true", required=True)
    budget_compute.set_defaults(func=handle_budget_compute)
    budget_run = budget_subparsers.add_parser("run", help="Persist a risk budget run.")
    _add_budget_args(budget_run)
    budget_run.set_defaults(func=handle_budget_run)
    budget_latest = budget_subparsers.add_parser("latest", help="Show latest budget.")
    budget_latest.add_argument("--json", action="store_true")
    budget_latest.set_defaults(func=handle_budget_latest)

    factor_parser = risk_subparsers.add_parser("factor", help="Factor exposure.")
    factor_subparsers = factor_parser.add_subparsers(dest="factor_command", required=True)
    factor_latest = factor_subparsers.add_parser("latest", help="Show latest factor snapshot.")
    factor_latest.add_argument("--portfolio-id", default="default")
    factor_latest.add_argument("--lookback-window", type=int, default=60)
    factor_latest.set_defaults(func=handle_factor_latest)
    factor_history = factor_subparsers.add_parser("history", help="List factor snapshots.")
    factor_history.add_argument("--since", required=True)
    factor_history.add_argument("--portfolio-id", default="default")
    factor_history.add_argument("--lookback-window", type=int, default=60)
    factor_history.add_argument("--limit", type=_positive_int, default=50)
    factor_history.set_defaults(func=handle_factor_history)
    factor_run = factor_subparsers.add_parser("run", help="Run empty-position factor preview.")
    factor_run.add_argument("--as-of", required=True)
    factor_run.add_argument("--portfolio-id", default="default")
    factor_run.add_argument("--dry-run", action="store_true", required=True)
    factor_run.set_defaults(func=handle_factor_run)

    correlation_parser = risk_subparsers.add_parser("correlation", help="Correlation state.")
    correlation_subparsers = correlation_parser.add_subparsers(
        dest="correlation_command", required=True
    )
    correlation_current = correlation_subparsers.add_parser(
        "current", help="Show latest correlation snapshot."
    )
    correlation_current.add_argument("--portfolio-id", default="default")
    correlation_current.add_argument("--snapshot-type", default="symbol")
    correlation_current.add_argument("--lookback-window", type=int, default=60)
    correlation_current.add_argument("--json", action="store_true")
    correlation_current.set_defaults(func=handle_correlation_current)

    mv_parser = risk_subparsers.add_parser("mv-optimize", help="Advisory MVO dry-run.")
    mv_parser.add_argument("--strategy-id", action="append", dest="strategy_ids", required=True)
    mv_parser.add_argument("--objective", default="minimum_variance")
    mv_parser.add_argument("--dry-run", action="store_true", required=True)
    mv_parser.set_defaults(func=handle_mv_optimize)

    verify_parser = risk_subparsers.add_parser(
        "verify-parameter-effects", help="Verify risk settings affect replay runtime."
    )
    verify_parser.add_argument("--controls", required=True, type=Path)
    verify_parser.add_argument("--settings", required=True, type=Path)
    verify_parser.add_argument("--symbols", required=True)
    verify_parser.add_argument("--start", required=True)
    verify_parser.add_argument("--end", required=True)
    verify_parser.add_argument("--starting-cash", type=Decimal, default=Decimal("100000"))
    verify_parser.add_argument("--random-seed", type=int, default=42)
    verify_parser.add_argument("--reset-sim-state", action="store_true")
    verify_parser.add_argument("--print-summary", action="store_true")
    verify_parser.add_argument("--output", type=Path)
    verify_parser.add_argument(
        "--parameter",
        action="append",
        dest="parameters",
        choices=[
            "max_portfolio_drawdown",
            "max_strategy_drawdown",
            "risk_tolerance",
            "max_capital_per_strategy",
            "target_portfolio_volatility",
        ],
    )
    verify_parser.set_defaults(func=handle_verify_parameter_effects)

    audit_parser = risk_subparsers.add_parser("audit-log", help="Show risk audit events.")
    audit_parser.add_argument("--limit", type=_positive_int, default=20)
    audit_parser.set_defaults(func=handle_audit_log)

    export_parser = risk_subparsers.add_parser("export", help="Write risk artifact.")
    export_parser.add_argument("--output", required=True, type=Path)
    export_parser.set_defaults(func=handle_export)


def handle_limits_show(args: argparse.Namespace) -> int:
    print_json(_limits_payload())
    return 0


def handle_snapshot_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = RiskSnapshotRepository(session).get_latest()
        print_json(
            {"found": row is not None, "snapshot": _risk_snapshot_payload(row) if row else None}
        )
        return 0
    finally:
        session.close()


def handle_snapshot_history(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = RiskSnapshotRepository(session).list_recent(limit=args.limit)
        print_json(
            {"snapshots": [_risk_snapshot_payload(row) for row in rows], "limit": args.limit}
        )
        return 0
    finally:
        session.close()


def handle_snapshot_compute(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        snapshot = RiskSnapshotService().compute_snapshot(
            run_id=UUID(args.run_id),
            timestamp=parse_datetime(args.as_of),
            position_snapshot=None,
            cash_snapshot=None,
            limits_config=_risk_limit_config(),
        )
        row_payload = snapshot.model_dump(mode="json")
        if args.persist:
            row = RiskSnapshotRepository(session).upsert(snapshot)
            session.commit()
            print_json({"persisted": True, "snapshot": _risk_snapshot_payload(row)})
        else:
            session.rollback()
            print_json({"persisted": False, "snapshot": row_payload})
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Risk snapshot compute failed: {exc}")
        return 1
    finally:
        session.close()


def handle_exposure_current(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = RiskSnapshotRepository(session).get_latest()
        print_json(
            {
                "source": "latest_risk_snapshot",
                "found": row is not None,
                "exposure": _exposure_payload(row) if row else _empty_exposure_payload(),
            }
        )
        return 0
    finally:
        session.close()


def handle_concentration_current(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = RiskSnapshotRepository(session).get_latest()
        utilization = row.utilization if row else {}
        print_json(
            {
                "source": "latest_risk_snapshot",
                "found": row is not None,
                "portfolio_symbol_concentration": utilization.get("portfolio_symbol_concentration"),
                "sector_concentration": utilization.get("sector_concentration"),
                "top": args.top,
            }
        )
        return 0
    finally:
        session.close()


def handle_pretrade_check(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        settings = Settings()
        now = datetime.now(UTC)
        intent = OrderIntent(
            intent_id=_uuid4(),
            idempotency_key="risk-cli-pretrade-check",
            run_id=_uuid4(),
            strategy_id=args.strategy_id,
            timestamp=now,
            bar_timestamp=now,
            symbol=args.symbol.upper(),
            side=args.side,
            qty=args.qty,
            notional=None,
            order_type=OrderType.LIMIT,
            limit_price=args.limit_price,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=f"risk-cli-pretrade-{now.timestamp()}",
            metadata={"source": "risk_cli_pretrade_check"},
        )
        PreTradeRiskService(
            settings=settings,
            risk_state_reader=StubRiskStateReader(),
            audit_log_repo=AuditLogRepository(session),
        ).assert_order_allowed(intent, now=now)
        session.rollback()
        print_json(
            {
                "status": "allowed",
                "submitted_to_broker": False,
                "persisted": False,
                "assumptions": {"risk_state_reader": "stub_zero_exposure"},
                "symbol": intent.symbol,
                "side": intent.side.value,
                "qty": str(intent.qty),
                "limit_price": str(intent.limit_price),
            }
        )
        return 0
    except Exception as exc:
        session.rollback()
        print_json({"status": "blocked", "submitted_to_broker": False, "error": str(exc)})
        return 1
    finally:
        session.close()


def handle_drawdown_list(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = DrawdownGovernanceLadderStateRepository(session)
        rows = repo.get_by_state(args.state) if args.state else repo.get_all()
        print_json({"states": [_drawdown_state_payload(row) for row in rows], "total": len(rows)})
        return 0
    finally:
        session.close()


def handle_drawdown_show(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = DrawdownGovernanceLadderStateRepository(session).get_for_strategy(args.strategy_id)
        print_json(
            {"found": row is not None, "state": _drawdown_state_payload(row) if row else None}
        )
        return 0 if row is not None else 1
    finally:
        session.close()


def handle_drawdown_transitions(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = ddg_transition_repository.DrawdownGovernanceLadderTransitionRepository(
            session
        ).get_recent_for_strategy(args.strategy_id, limit=args.limit)
        print_json({"transitions": [_drawdown_transition_payload(row) for row in rows]})
        return 0
    finally:
        session.close()


def handle_drawdown_pending_ack(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = DrawdownGovernanceLadderStateRepository(session).get_pending_operator_ack()
        print_json({"states": [_drawdown_state_payload(row) for row in rows], "total": len(rows)})
        return 0
    finally:
        session.close()


def handle_drawdown_acknowledge(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        acknowledged = DrawdownGovernanceService(session=session).acknowledge_breach(
            args.strategy_id,
            operator=args.operator,
            reason=args.reason,
        )
        print_json({"strategy_id": args.strategy_id, "acknowledged": acknowledged})
        return 0 if acknowledged else 1
    except Exception as exc:
        session.rollback()
        print_error(f"Drawdown acknowledgement failed: {exc}")
        return 1
    finally:
        session.close()


def handle_drawdown_evaluate(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        if args.dry_run:
            print_json(
                {
                    "dry_run": True,
                    "persisted": False,
                    "status": "preview_only",
                    "note": (
                        "DrawdownGovernanceService.run persists internally; dry-run reports "
                        "current ladder state and effective config without evaluation."
                    ),
                    "config": _drawdown_config_payload(session),
                    "current_states": [
                        _drawdown_state_payload(row)
                        for row in DrawdownGovernanceLadderStateRepository(session).get_all()
                    ],
                }
            )
            return 0
        result = DrawdownGovernanceService(session=session).run(run_id=args.run_id)
        print_json(DrawdownGovernanceService.result_to_jsonable(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Drawdown evaluation failed: {exc}")
        return 1
    finally:
        session.close()


def handle_budget_compute(args: argparse.Namespace) -> int:
    return _run_budget(args, persist=False)


def handle_budget_run(args: argparse.Namespace) -> int:
    return _run_budget(args, persist=True)


def handle_budget_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = RiskBudgetingService(session=session).get_latest_snapshot()
        print_json(
            {"found": row is not None, "snapshot": _risk_budget_payload(row) if row else None}
        )
        return 0
    finally:
        session.close()


def handle_factor_latest(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = FactorExposureMonitoringService(session=session).get_latest_snapshot(
            portfolio_id=args.portfolio_id,
            lookback_window=args.lookback_window,
        )
        print_json(
            {"found": row is not None, "snapshot": _factor_snapshot_payload(row) if row else None}
        )
        return 0
    finally:
        session.close()


def handle_factor_history(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        rows = FactorExposureMonitoringService(session=session).get_history(
            since=parse_datetime(args.since),
            portfolio_id=args.portfolio_id,
            lookback_window=args.lookback_window,
            limit=args.limit,
        )
        print_json({"snapshots": [_factor_snapshot_payload(row) for row in rows]})
        return 0
    finally:
        session.close()


def handle_factor_run(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        result = FactorExposureMonitoringService(
            session=session,
            config=FactorExposureMonitoringConfig(
                factor_exposure_windows=[60], minimum_observations_required=20
            ),
        ).run(
            as_of=parse_datetime(args.as_of),
            positions=[],
            portfolio_id=args.portfolio_id,
        )
        session.rollback()
        print_json(FactorExposureMonitoringService.result_to_jsonable(result))
        return 0
    finally:
        session.close()


def handle_correlation_current(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        row = CorrelationSnapshotRepository(session).get_latest_correlation(
            snapshot_type=args.snapshot_type,
            lookback_window=args.lookback_window,
        )
        print_json(
            {
                "found": row is not None,
                "observability_only": True,
                "snapshot": _correlation_snapshot_payload(row) if row else None,
            }
        )
        return 0
    finally:
        session.close()


def handle_mv_optimize(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        objective = _parse_objective(args.objective)
        expected_returns = {strategy_id: 0.0 for strategy_id in args.strategy_ids}
        result = MeanVarianceOptimizer(
            session=session,
            config=MeanVarianceConfig(dry_run=True),
        ).optimize(
            assets=args.strategy_ids,
            expected_returns=expected_returns,
            objective=objective,
        )
        session.rollback()
        print_json(MeanVarianceOptimizer.result_to_jsonable(result))
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Mean-variance optimization failed: {exc}")
        return 1
    finally:
        session.close()


def handle_verify_parameter_effects(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_risk_parameter_effects,
    )

    result = handle_verify_risk_parameter_effects(args)
    if args.output:
        _write_payload(
            args.output,
            {
                "command": "risk verify-parameter-effects",
                "exit_code": result,
                "controls": str(args.controls),
                "settings": str(args.settings),
                "symbols": args.symbols,
                "start": args.start,
                "end": args.end,
                "parameters": args.parameters,
                "external_broker": False,
                "written_at": datetime.now(UTC).isoformat(),
            },
        )
    return result


def handle_audit_log(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        events: list[dict[str, Any]] = []
        repo = AuditLogRepository(session)
        for action in sorted(_RISK_AUDIT_EVENT_TYPES):
            rows, _total = repo.list_events(page=1, page_size=args.limit, action_type=action)
            events.extend(_audit_event_payload(row) for row in rows)
        events.sort(key=lambda row: row["event_timestamp"], reverse=True)
        print_json({"events": events[: args.limit], "limit": args.limit})
        return 0
    finally:
        session.close()


def handle_export(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        latest_snapshot = RiskSnapshotRepository(session).get_latest()
        latest_budget = RiskBudgetingService(session=session).get_latest_snapshot()
        payload = {
            "exported_at": datetime.now(UTC).isoformat(),
            "limits": _limits_payload(session=session),
            "latest_risk_snapshot": (
                _risk_snapshot_payload(latest_snapshot) if latest_snapshot else None
            ),
            "drawdown_states": [
                _drawdown_state_payload(row)
                for row in DrawdownGovernanceLadderStateRepository(session).get_all()
            ],
            "latest_risk_budget": _risk_budget_payload(latest_budget) if latest_budget else None,
        }
        _write_payload(args.output, payload)
        print_json({"wrote": str(args.output)})
        return 0
    finally:
        session.close()


def _run_budget(args: argparse.Namespace, *, persist: bool) -> int:
    session = get_session()
    try:
        result = RiskBudgetingService(
            session=session,
            config=RiskBudgetConfig(mode=AllocationMode(args.mode)),
        ).compute(strategy_ids=args.strategy_ids)
        if persist:
            session.commit()
        else:
            session.rollback()
        print_json({"persisted": persist, **RiskBudgetingService.result_to_jsonable(result)})
        return 0
    except Exception as exc:
        session.rollback()
        print_error(f"Risk budget computation failed: {exc}")
        return 1
    finally:
        session.close()


def _limits_payload(session=None) -> dict[str, Any]:
    settings_payload: dict[str, Any] = {}
    try:
        settings = Settings()
        settings_payload = {
            "max_gross_exposure": settings.max_gross_exposure,
            "max_net_exposure": settings.max_net_exposure,
            "max_leverage": settings.max_leverage,
            "max_symbol_exposure": settings.max_symbol_exposure,
            "max_portfolio_symbol_exposure_usd": settings.max_portfolio_symbol_exposure_usd,
            "max_portfolio_symbol_pct": settings.max_portfolio_symbol_pct,
            "max_sector_exposure_pct": settings.max_sector_exposure_pct,
            "default_max_sector_exposure_pct": settings.default_max_sector_exposure_pct,
            "unknown_sector_policy": settings.unknown_sector_policy,
            "source": "environment Settings",
        }
    except Exception as exc:
        settings_payload = {"source": "environment Settings", "error": str(exc)}

    owns_session = session is None
    if session is None:
        session = get_session()
    try:
        row = OperatorSettingsRepository(session).get_current()
        operator_settings = (
            {
                "max_drawdown_limit": row.max_drawdown_limit,
                "max_strategy_drawdown": row.max_strategy_drawdown,
                "target_portfolio_volatility": row.target_portfolio_volatility,
                "max_total_strategy_allocation_pct": row.max_total_strategy_allocation_pct,
                "source": "operator_settings",
            }
            if row
            else {"source": "operator_settings", "found": False}
        )
    finally:
        if owns_session:
            session.close()

    return {
        "environment_limits": settings_payload,
        "operator_settings_limits": operator_settings,
        "source_of_truth": {
            "exposure_limits": "environment Settings",
            "drawdown_limits": "operator_settings",
            "allocation_budget": "operator_settings + capital_allocation_policies",
            "sector_limits": "environment Settings",
        },
    }


def _risk_limit_config() -> RiskLimitConfig:
    try:
        settings = Settings()
        return RiskLimitConfig(
            max_gross_exposure=Decimal(str(settings.max_gross_exposure)),
            max_net_exposure=Decimal(str(settings.max_net_exposure)),
            max_leverage=Decimal(str(settings.max_leverage)),
            max_portfolio_symbol_exposure_usd=(
                Decimal(str(settings.max_portfolio_symbol_exposure_usd))
                if settings.max_portfolio_symbol_exposure_usd is not None
                else None
            ),
            max_portfolio_symbol_pct=(
                Decimal(str(settings.max_portfolio_symbol_pct))
                if settings.max_portfolio_symbol_pct is not None
                else None
            ),
            max_sector_exposure_pct=settings.max_sector_exposure_pct or {},
            default_max_sector_exposure_pct=settings.default_max_sector_exposure_pct,
            unknown_sector_policy=settings.unknown_sector_policy,
        )
    except Exception:
        return RiskLimitConfig()


def _risk_snapshot_payload(row: RiskSnapshotRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "snapshot_id": str(row.snapshot_id),
        "run_id": str(row.run_id),
        "timestamp": _iso(row.timestamp),
        "gross_exposure": row.gross_exposure,
        "net_exposure": row.net_exposure,
        "leverage": row.leverage,
        "drawdown_pct": row.drawdown_pct,
        "limits": row.limits,
        "utilization": row.utilization,
        "is_blocked": row.is_blocked,
        "block_reasons": row.block_reasons,
    }


def _exposure_payload(row: RiskSnapshotRow | None) -> dict[str, Any]:
    if row is None:
        return _empty_exposure_payload()
    return {
        "gross_exposure": row.gross_exposure,
        "net_exposure": row.net_exposure,
        "leverage": row.leverage,
        "drawdown_pct": row.drawdown_pct,
        "is_blocked": row.is_blocked,
        "block_reasons": row.block_reasons,
    }


def _empty_exposure_payload() -> dict[str, Any]:
    return {
        "gross_exposure": "0",
        "net_exposure": "0",
        "leverage": 0.0,
        "drawdown_pct": None,
        "is_blocked": False,
        "block_reasons": None,
    }


def _drawdown_state_payload(row) -> dict[str, Any]:
    return {
        "strategy_id": row.strategy_id,
        "ladder_state": row.ladder_state,
        "previous_ladder_state": row.previous_ladder_state,
        "transition_reason": row.transition_reason,
        "realized_drawdown": row.realized_drawdown,
        "max_drawdown_allowed": row.max_drawdown_allowed,
        "drawdown_utilization": row.drawdown_utilization,
        "allocation_scalar": row.allocation_scalar,
        "cooldown_expires_at": _iso(row.cooldown_expires_at),
        "evaluation_count": row.evaluation_count,
        "operator_ack_required": row.operator_ack_required,
        "operator_acknowledged_at": _iso(row.operator_acknowledged_at),
        "operator_acknowledged_by": row.operator_acknowledged_by,
        "last_transition_at": _iso(row.last_transition_at),
        "last_evaluated_at": _iso(row.last_evaluated_at),
        "updated_at": _iso(row.updated_at),
    }


def _drawdown_transition_payload(row) -> dict[str, Any]:
    return {
        "transition_id": row.transition_id,
        "strategy_id": row.strategy_id,
        "from_state": row.from_state,
        "to_state": row.to_state,
        "transition_reason": row.transition_reason,
        "triggered_by": row.triggered_by,
        "realized_drawdown": row.realized_drawdown,
        "max_drawdown_allowed": row.max_drawdown_allowed,
        "drawdown_utilization": row.drawdown_utilization,
        "allocation_scalar_after": row.allocation_scalar_after,
        "cooldown_expires_at": _iso(row.cooldown_expires_at),
        "evaluation_run_id": row.evaluation_run_id,
        "created_at": _iso(row.created_at),
    }


def _drawdown_config_payload(session) -> dict[str, Any]:
    cfg = DrawdownGovernanceService(session=session)._load_config()
    return dict(cfg.__dict__)


def _risk_budget_payload(row) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "run_id": row.run_id,
        "computed_at": _iso(row.computed_at),
        "mode": row.mode,
        "strategy_universe": row.strategy_universe,
        "strategy_count": row.strategy_count,
        "recommended_weights": row.recommended_weights,
        "realized_risk_contributions": row.realized_risk_contributions,
        "portfolio_volatility_estimate": row.portfolio_volatility_estimate,
        "diversification_ratio": row.diversification_ratio,
        "fallback_used": row.fallback_used,
        "fallback_reason": row.fallback_reason,
        "hidden_concentration_detected": row.hidden_concentration_detected,
        "warnings": row.warnings,
    }


def _factor_snapshot_payload(row) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "run_id": row.run_id,
        "portfolio_id": row.portfolio_id,
        "computed_at": _iso(row.computed_at),
        "as_of_date": _iso(row.as_of_date),
        "lookback_window": row.lookback_window,
        "benchmark_symbol": row.benchmark_symbol,
        "portfolio_exposures": row.portfolio_exposures,
        "sector_exposures": row.sector_exposures,
        "concentration_diagnostics": row.concentration_diagnostics,
        "warnings": row.warnings,
        "duration_seconds": row.duration_seconds,
    }


def _correlation_snapshot_payload(row) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "snapshot_type": row.snapshot_type,
        "lookback_window": row.lookback_window,
        "computed_at": _iso(row.computed_at),
        "as_of_date": _iso(row.as_of_date),
        "asset_universe": row.asset_universe,
        "asset_count": row.asset_count,
        "average_pairwise_correlation": row.average_pairwise_correlation,
        "max_pairwise_correlation": row.max_pairwise_correlation,
        "min_pairwise_correlation": row.min_pairwise_correlation,
        "clusters": row.clusters,
        "warnings": row.warnings,
    }


def _audit_event_payload(row) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "event_type": row.event_type,
        "component": row.component,
        "event_timestamp": _iso(row.event_timestamp),
        "message": row.message,
        "metadata": row.metadata_,
    }


def _add_budget_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy-id", action="append", dest="strategy_ids", required=True)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in AllocationMode],
        default=AllocationMode.EQUAL_RISK_CONTRIBUTION.value,
    )


def _parse_side(value: str) -> Side:
    normalized = value.strip().upper()
    try:
        return Side(normalized)
    except ValueError:
        try:
            return Side(normalized.lower())
        except ValueError as exc:
            raise argparse.ArgumentTypeError("side must be buy or sell") from exc


def _parse_objective(value: str) -> OptimizationObjective:
    aliases = {"min_variance": "minimum_variance"}
    normalized = aliases.get(value, value)
    return OptimizationObjective(normalized)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _uuid4():
    from uuid import uuid4

    return uuid4()


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(_jsonable(payload), sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(_jsonable(payload), indent=2, default=str), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
