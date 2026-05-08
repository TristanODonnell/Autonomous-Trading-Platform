from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    ExecutionContext,
    build_execution_context,
)
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.safety.contexts.build_safety_context import (
    build_safety_context,
)
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import (
    StubOrderActivityReader,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.scheduler.jobs.run_order_reconciliation_job import (
    run_order_reconciliation_job,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill as SorFill
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def to_sor_fill(fill: Fill) -> SorFill:
    return SorFill(
        fill_id=fill.fill_id,
        order_id=fill.order_id,
        broker_fill_id=fill.broker_fill_id,
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        filled_at=fill.filled_at,
        commission=fill.commission,
        fees=fill.fees,
        liquidity=fill.liquidity,
    )


@dataclass
class ExecutionCliDependencies:
    execution_context: ExecutionContext


def build_cli_execution_dependencies(session: Session) -> ExecutionCliDependencies:
    settings = Settings()

    audit_log_repository = AuditLogRepository(session)
    environment_policy = EnvironmentSafetyPolicy(settings=settings)

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_policy,
        risk_state_reader=StubRiskStateReader(),
        order_activity_reader=StubOrderActivityReader(),
        audit_log_repository=audit_log_repository,
    )
    portfolio_engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(session),
        overrides_repo=AllocationOverridesRepository(session),
        promotion_rules_repo=PromotionRulesRepository(session),
        total_capital=float(settings.initial_capital),
    )
    execution_context = build_execution_context(
        session=session,
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_log_repository,
        alpaca_settings=settings,
        portfolio_engine=portfolio_engine,
    )

    return ExecutionCliDependencies(
        execution_context=execution_context,
    )


def register(subparsers) -> None:
    execution_parser = subparsers.add_parser("execution", help="Execution operations")
    execution_subparsers = execution_parser.add_subparsers(
        dest="execution_command",
        required=True,
    )

    reconcile_order_parser = execution_subparsers.add_parser(
        "reconcile-order",
        help="Reconcile a single order",
    )
    reconcile_order_parser.add_argument("--order-id", required=True)
    reconcile_order_parser.set_defaults(func=handle_reconcile_order)

    reconcile_open_orders_parser = execution_subparsers.add_parser(
        "reconcile-open-orders",
        help="Reconcile all open orders",
    )
    reconcile_open_orders_parser.set_defaults(func=handle_reconcile_open_orders)

    inspect_order_parser = execution_subparsers.add_parser(
        "inspect-order",
        help="Inspect order",
    )
    inspect_order_parser.add_argument("--order-id", required=True)
    inspect_order_parser.set_defaults(func=handle_inspect_order)

    inspect_position_parser = execution_subparsers.add_parser(
        "inspect-position",
        help="Inspect position",
    )
    inspect_position_parser.add_argument("--symbol", required=True)
    inspect_position_parser.set_defaults(func=handle_inspect_position)

    inspect_cash_parser = execution_subparsers.add_parser(
        "inspect-cash",
        help="Inspect cash snapshot",
    )
    inspect_cash_parser.set_defaults(func=handle_inspect_cash)


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _model_to_dict(obj) -> dict:
    if obj is None:
        return {}

    data: dict[str, object] = {}
    for column in obj.__table__.columns:
        data[column.name] = _serialize_value(getattr(obj, column.name))
    return data


def handle_reconcile_order(args: argparse.Namespace) -> int:
    session = get_session()
    now_utc = datetime.now(UTC)

    print_header("Reconcile Order")
    try:
        deps = build_cli_execution_dependencies(session)
        ctx = deps.execution_context

        with SorUnitOfWork(session) as uow:
            tracked_order = uow.tracked_orders.get_by_order_id(args.order_id)

            if tracked_order is None:
                print_json(
                    {
                        "status": "not_found",
                        "order_id": args.order_id,
                        "message": "tracked order not found",
                    }
                )
                return 1

            result = ctx.order_reconciliation_service.reconcile_order(
                tracked_order=tracked_order,
            )

            if getattr(result, "broker_order", None) is not None:
                uow.broker_orders.upsert(result.broker_order)

            ctx.order_runtime_state_service.apply_reconciliation_result(
                uow=uow, result=result, now_utc=now_utc
            )

            fill_payload = None
            risk_snapshot = None

            fill = getattr(result, "fill", None)
            if fill is not None:
                sor_fill = to_sor_fill(fill)

                uow.fills.upsert(sor_fill)

                accounting_result = ctx.post_fill_accounting_service.apply_fill(
                    uow=uow,
                    fill=sor_fill,
                    now_utc=now_utc,
                )

                fill_payload = {
                    "fill": _model_to_dict(sor_fill),
                    "position_snapshot": _model_to_dict(
                        getattr(accounting_result, "position_snapshot", None)
                    ),
                    "cash_snapshot": _model_to_dict(
                        getattr(accounting_result, "cash_snapshot", None)
                    ),
                }

                risk_snapshot = ctx.risk_snapshot_service.compute_snapshot(
                    run_id=tracked_order.run_id,
                    timestamp=now_utc,
                    position_snapshot=getattr(accounting_result, "position_snapshot", None),
                    cash_snapshot=getattr(accounting_result, "cash_snapshot", None),
                )
                uow.risk_snapshots.upsert(risk_snapshot)
            else:
                fill_payload = None

        print_json(
            {
                "status": "ok",
                "order_id": args.order_id,
                "broker_order": _model_to_dict(getattr(result, "broker_order", None)),
                "fill_result": fill_payload,
                "risk_snapshot": _model_to_dict(risk_snapshot)
                if risk_snapshot is not None
                else None,
            }
        )
        return 0
    finally:
        session.close()


def handle_reconcile_open_orders(
    _args: argparse.Namespace,
    run_id: str,
) -> int:
    print_header("Reconcile Open Orders")
    now_utc = datetime.now(UTC)
    run_order_reconciliation_job(run_id=run_id, now_utc=now_utc)
    print_json(
        {
            "status": "ok",
            "reconciled_at": now_utc.isoformat(),
        }
    )
    return 0


def handle_inspect_order(args: argparse.Namespace) -> int:
    print_header("Inspect Order")

    session = get_session()
    try:
        with SorUnitOfWork(session) as uow:
            tracked_order = uow.tracked_orders.get_by_order_id(args.order_id)

            if tracked_order is None:
                print_json(
                    {
                        "status": "not_found",
                        "order_id": args.order_id,
                        "message": "tracked order not found",
                    }
                )
                return 1

            broker_order = None
            broker_order_id = getattr(tracked_order, "broker_order_id", None)

            if broker_order_id:
                broker_order = uow.broker_orders.get_by_broker_order_id(broker_order_id)

            if broker_order is None:
                broker_order = uow.broker_orders.get_latest_for_order_id(args.order_id)

            fills = []
            if hasattr(uow, "fills") and hasattr(uow.fills, "list_for_order_id"):
                fills = uow.fills.list_for_order_id(args.order_id)

        print_json(
            {
                "status": "ok",
                "order_id": args.order_id,
                "tracked_order": _model_to_dict(tracked_order),
                "broker_order": _model_to_dict(broker_order) if broker_order else None,
                "fills": [_model_to_dict(fill) for fill in fills],
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_position(args: argparse.Namespace) -> int:
    print_header("Inspect Position")

    session = get_session()
    try:
        with SorUnitOfWork(session) as uow:
            position = uow.position_snapshots.get_latest_for_symbol(args.symbol)

        if position is None:
            print_json(
                {
                    "status": "not_found",
                    "symbol": args.symbol,
                    "message": "no position snapshot found for symbol",
                }
            )
            return 1

        print_json(
            {
                "status": "ok",
                "symbol": args.symbol,
                "position_snapshot": _model_to_dict(position),
            }
        )
        return 0
    finally:
        session.close()


def handle_inspect_cash(_args: argparse.Namespace) -> int:
    print_header("Inspect Cash")

    session = get_session()
    try:
        with SorUnitOfWork(session) as uow:
            cash_snapshot = uow.cash_snapshots.get_latest()

        if cash_snapshot is None:
            print_json(
                {
                    "status": "not_found",
                    "message": "no cash snapshot found",
                }
            )
            return 1

        print_json(
            {
                "status": "ok",
                "cash_snapshot": _model_to_dict(cash_snapshot),
            }
        )
        return 0
    finally:
        session.close()
