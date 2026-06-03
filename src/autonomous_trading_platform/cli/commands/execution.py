from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.formatters import print_header, print_json
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
    PolicyMode,
)
from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.clients.alpaca_order_stream_client import (
    AlpacaOrderStreamClient,
)
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    ExecutionContext,
    build_execution_context,
)
from autonomous_trading_platform.execution.policy.execution_policy_engine import (
    ExecutionPolicyEngine,
)
from autonomous_trading_platform.execution.services.broker_event_stream_service import (
    BrokerEventStreamService,
)
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.execution.services.broker_startup_health_check_service import (
    BrokerStartupHealthCheckService,
)
from autonomous_trading_platform.execution.services.external_broker_reconciliation_service import (
    ExternalBrokerReconciliationService,
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
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics
from autonomous_trading_platform.storage.sor.models.fills import Fill as SorFill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.fill_quality_metrics_repository import (
    FillQualityMetricsRepository,
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


class _NoOpAuditLogger:
    def add(self, _audit_log: AuditLogEvent) -> None:
        return None


class _StaticQuoteBrokerClient:
    def __init__(self, *, mid_price: Decimal) -> None:
        self.mid_price = mid_price

    def get_latest_quotes(self, symbols: list[str]) -> dict[str, dict[str, str]]:
        return {
            symbol: {
                "ask_price": str(self.mid_price),
                "bid_price": str(self.mid_price),
            }
            for symbol in symbols
        }


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
        session=session,
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


def _build_broker_client(settings: Settings) -> AlpacaBrokerClient:
    return AlpacaBrokerClient(settings)


def _build_broker_runtime_sync_service(
    *,
    session: Session,
    settings: Settings,
) -> BrokerRuntimeSyncService:
    return BrokerRuntimeSyncService(
        session=session,
        broker_client=_build_broker_client(settings),
        settings=settings,
    )


def _add_broker_safety_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--paper-only",
        action="store_true",
        help="Refuse to run unless TRADING_ENVIRONMENT=paper",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit the command when TRADING_ENVIRONMENT=live",
    )


def _add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", help="Write the JSON result to this artifact path")


def _assert_broker_cli_safety(settings: Settings, args: argparse.Namespace) -> None:
    trading_environment = settings.trading_environment
    if getattr(args, "paper_only", False) and trading_environment is not TradingEnvironment.PAPER:
        raise RuntimeError(
            "Refusing broker CLI command because --paper-only was supplied and "
            f"TRADING_ENVIRONMENT={trading_environment.value}."
        )
    if trading_environment is TradingEnvironment.LIVE and not getattr(args, "allow_live", False):
        raise RuntimeError(
            "Refusing broker CLI command in live mode. Re-run with --allow-live only after "
            "confirming live credentials, controls, and account scope."
        )


def _parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UUID: {value}") from exc


def _parse_alpaca_broker_order_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except ValueError as exc:
        raise ValueError(
            "broker order id must be the real Alpaca order UUID, not a placeholder. "
            "Use `execution sync-open-orders ...` or `execution list-open-orders --source broker` "
            "to find broker_order_id values."
        ) from exc


def _write_output_if_requested(args: argparse.Namespace, payload: dict) -> None:
    output = getattr(args, "output", None)
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _emit_json_result(args: argparse.Namespace, payload: dict) -> None:
    _write_output_if_requested(args, payload)
    print_json(payload)


def _record_execution_cli_audit_event(
    session: Session,
    *,
    action: str,
    run_id: str | None,
    message: str,
    metadata: dict,
    occurred_at: datetime,
) -> str:
    event_id = str(uuid4())
    AuditLogRepository(session).add(
        AuditLogEvent(
            event_id=event_id,
            run_id=run_id,
            event_type=action,
            component="execution.cli",
            event_timestamp=occurred_at,
            message=message,
            metadata=metadata,
        )
    )
    session.commit()
    return event_id


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
    reconcile_order_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch broker state and calculate the reconciliation result without persisting",
    )
    _add_broker_safety_options(reconcile_order_parser)
    _add_output_option(reconcile_order_parser)
    reconcile_order_parser.set_defaults(func=handle_reconcile_order)

    reconcile_open_orders_parser = execution_subparsers.add_parser(
        "reconcile-open-orders",
        help="Reconcile all open orders",
    )
    reconcile_open_orders_parser.add_argument("--run-id", required=True)
    reconcile_open_orders_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report currently reconcilable local orders without running reconciliation",
    )
    _add_broker_safety_options(reconcile_open_orders_parser)
    _add_output_option(reconcile_open_orders_parser)
    reconcile_open_orders_parser.set_defaults(func=handle_reconcile_open_orders)

    broker_health_parser = execution_subparsers.add_parser(
        "broker-health",
        help="Verify broker account readiness",
    )
    _add_broker_safety_options(broker_health_parser)
    _add_output_option(broker_health_parser)
    broker_health_parser.set_defaults(func=handle_broker_health)

    sync_broker_state_parser = execution_subparsers.add_parser(
        "sync-broker-state",
        help="Sync broker account and cash state",
    )
    sync_broker_state_parser.add_argument("--run-id")
    _add_broker_safety_options(sync_broker_state_parser)
    _add_output_option(sync_broker_state_parser)
    sync_broker_state_parser.set_defaults(func=handle_sync_broker_state)

    validate_broker_consistency_parser = execution_subparsers.add_parser(
        "validate-broker-consistency",
        help="Compare latest persisted broker/cash snapshots against broker state",
    )
    validate_broker_consistency_parser.add_argument(
        "--tolerance",
        default="0.01",
        help="Decimal tolerance for cash/equity comparisons",
    )
    _add_broker_safety_options(validate_broker_consistency_parser)
    _add_output_option(validate_broker_consistency_parser)
    validate_broker_consistency_parser.set_defaults(func=handle_validate_broker_consistency)

    sync_position_snapshot_parser = execution_subparsers.add_parser(
        "sync-position-snapshot",
        help="Persist current broker positions into a position snapshot",
    )
    sync_position_snapshot_parser.add_argument("--run-id", required=True)
    _add_broker_safety_options(sync_position_snapshot_parser)
    _add_output_option(sync_position_snapshot_parser)
    sync_position_snapshot_parser.set_defaults(func=handle_sync_position_snapshot)

    sync_open_orders_parser = execution_subparsers.add_parser(
        "sync-open-orders",
        help="Persist broker open orders into broker order rows",
    )
    sync_open_orders_parser.add_argument("--run-id", required=True)
    sync_open_orders_parser.add_argument("--account-id", required=True)
    _add_broker_safety_options(sync_open_orders_parser)
    _add_output_option(sync_open_orders_parser)
    sync_open_orders_parser.set_defaults(func=handle_sync_open_orders)

    sync_order_status_parser = execution_subparsers.add_parser(
        "sync-order-status",
        help="Fetch one broker order status and persist the broker order row",
    )
    sync_order_status_parser.add_argument("--order-id", required=True)
    sync_order_status_parser.add_argument("--run-id", required=True)
    sync_order_status_parser.add_argument("--account-id", required=True)
    _add_broker_safety_options(sync_order_status_parser)
    _add_output_option(sync_order_status_parser)
    sync_order_status_parser.set_defaults(func=handle_sync_order_status)

    reconcile_order_fills_parser = execution_subparsers.add_parser(
        "reconcile-order-fills",
        help="Extract and persist incremental fills for one broker order",
    )
    reconcile_order_fills_parser.add_argument("--order-id", required=True)
    reconcile_order_fills_parser.add_argument("--run-id", required=True)
    reconcile_order_fills_parser.add_argument("--account-id", required=True)
    _add_broker_safety_options(reconcile_order_fills_parser)
    _add_output_option(reconcile_order_fills_parser)
    reconcile_order_fills_parser.set_defaults(func=handle_reconcile_order_fills)

    external_reconcile_parser = execution_subparsers.add_parser(
        "external-reconcile",
        help="Generate a broker-vs-platform reconciliation report",
    )
    external_reconcile_parser.add_argument("--run-id", required=True)
    external_reconcile_parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist reconciliation checks into reconciliation_snapshots",
    )
    _add_broker_safety_options(external_reconcile_parser)
    _add_output_option(external_reconcile_parser)
    external_reconcile_parser.set_defaults(func=handle_external_reconcile)

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

    inspect_fill_quality_parser = execution_subparsers.add_parser(
        "inspect-fill-quality",
        help="Inspect fill-quality metrics by intent id",
    )
    inspect_fill_quality_parser.add_argument("--intent-id", required=True)
    _add_output_option(inspect_fill_quality_parser)
    inspect_fill_quality_parser.set_defaults(func=handle_inspect_fill_quality)

    list_fill_quality_parser = execution_subparsers.add_parser(
        "list-fill-quality",
        help="List fill-quality metrics for a run",
    )
    list_fill_quality_parser.add_argument("--run-id", required=True)
    list_fill_quality_parser.add_argument("--adverse-only", action="store_true")
    list_fill_quality_parser.add_argument("--limit", type=int, default=50)
    _add_output_option(list_fill_quality_parser)
    list_fill_quality_parser.set_defaults(func=handle_list_fill_quality)

    inspect_runtime_state_parser = execution_subparsers.add_parser(
        "inspect-runtime-state",
        help="Inspect strategy runtime state",
    )
    inspect_runtime_state_parser.add_argument("--strategy-id", required=True)
    _add_output_option(inspect_runtime_state_parser)
    inspect_runtime_state_parser.set_defaults(func=handle_inspect_runtime_state)

    list_open_orders_parser = execution_subparsers.add_parser(
        "list-open-orders",
        help="List open platform and/or broker orders",
    )
    list_open_orders_parser.add_argument(
        "--source",
        choices=["platform", "broker", "both"],
        default="platform",
    )
    _add_broker_safety_options(list_open_orders_parser)
    _add_output_option(list_open_orders_parser)
    list_open_orders_parser.set_defaults(func=handle_list_open_orders)

    cancel_order_parser = execution_subparsers.add_parser(
        "cancel-order",
        help="Cancel a broker order with explicit confirmation",
    )
    cancel_order_parser.add_argument("--broker-order-id", required=True)
    cancel_order_parser.add_argument("--dry-run", action="store_true")
    cancel_order_parser.add_argument(
        "--confirm-cancel",
        action="store_true",
        help="Required to send the broker cancellation request",
    )
    _add_broker_safety_options(cancel_order_parser)
    _add_output_option(cancel_order_parser)
    cancel_order_parser.set_defaults(func=handle_cancel_order)

    stream_orders_parser = execution_subparsers.add_parser(
        "stream-orders",
        help="Run the broker order stream briefly and report event health",
    )
    stream_orders_parser.add_argument("--duration-seconds", type=int, default=60)
    stream_orders_parser.add_argument("--dry-run", action="store_true")
    _add_broker_safety_options(stream_orders_parser)
    _add_output_option(stream_orders_parser)
    stream_orders_parser.set_defaults(func=handle_stream_orders)

    submit_intents_parser = execution_subparsers.add_parser(
        "submit-intents",
        help="Inspect pending order intents for a run before scheduler submission",
    )
    submit_intents_parser.add_argument("--run-id", required=True)
    submit_intents_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Only dry-run inspection is supported by this CLI harness",
    )
    _add_output_option(submit_intents_parser)
    submit_intents_parser.set_defaults(func=handle_submit_intents)

    policy_preview_parser = execution_subparsers.add_parser(
        "policy-preview",
        help="Preview execution policy transformation for one order intent",
    )
    policy_preview_parser.add_argument("--intent-id", required=True)
    policy_preview_parser.add_argument(
        "--policy-mode",
        choices=[mode.value for mode in PolicyMode],
        default=PolicyMode.PASSTHROUGH.value,
    )
    policy_preview_parser.add_argument("--offline", action="store_true")
    policy_preview_parser.add_argument("--mid-price")
    _add_broker_safety_options(policy_preview_parser)
    _add_output_option(policy_preview_parser)
    policy_preview_parser.set_defaults(func=handle_policy_preview)


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return _serialize_value(asdict(value))
    return value


def _model_to_dict(obj) -> dict:
    if obj is None:
        return {}

    data: dict[str, object] = {}
    for column in obj.__table__.columns:
        data[column.name] = _serialize_value(getattr(obj, column.name))
    return data


def _report_to_dict(report) -> dict[Any, Any]:
    return cast(dict[Any, Any], _serialize_value(report))


def _runtime_sync_result_to_dict(result) -> dict[Any, Any]:
    return cast(dict[Any, Any], _serialize_value(result))


def _order_intent_row_to_contract(row) -> OrderIntent:
    return OrderIntent(
        intent_id=row.intent_id,
        idempotency_key=row.idempotency_key,
        run_id=row.run_id,
        strategy_id=row.strategy_id,
        timestamp=row.timestamp,
        bar_timestamp=row.bar_timestamp,
        symbol=row.symbol,
        side=row.side,
        qty=row.qty,
        notional=row.notional,
        order_type=row.order_type,
        limit_price=row.limit_price,
        stop_price=row.stop_price,
        time_in_force=row.time_in_force,
        extended_hours=row.extended_hours,
        client_order_id=row.client_order_id,
        metadata=row.meta,
    )


def handle_reconcile_order(args: argparse.Namespace) -> int:
    session = get_session()
    now_utc = datetime.now(UTC)

    print_header("Reconcile Order")
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        deps = build_cli_execution_dependencies(session)
        ctx = deps.execution_context
        if args.dry_run:
            ctx.order_reconciliation_service.order_state_machine_service.audit_logger = cast(
                AuditLogRepository, _NoOpAuditLogger()
            )

        with SorUnitOfWork(session) as uow:
            tracked_order = uow.tracked_orders.get_by_order_id(
                _parse_uuid(args.order_id, field_name="order_id")
            )

            if tracked_order is None:
                _emit_json_result(
                    args,
                    {
                        "status": "not_found",
                        "order_id": args.order_id,
                        "message": "tracked order not found",
                    },
                )
                return 1

            result = ctx.order_reconciliation_service.reconcile_order(
                tracked_order=tracked_order,
            )

            broker_order = getattr(result, "broker_order", None)
            if broker_order is not None and not args.dry_run:
                uow.broker_orders.upsert(broker_order)

            if not args.dry_run:
                ctx.order_runtime_state_service.apply_reconciliation_result(
                    uow=uow, result=result, now_utc=now_utc
                )

            fill_payload = None
            risk_snapshot = None

            fill = getattr(result, "fill", None)
            if fill is not None:
                sor_fill = to_sor_fill(fill)

                if not args.dry_run:
                    uow.fills.upsert(sor_fill)

                fill_payload = {
                    "fill": _model_to_dict(sor_fill),
                    "position_snapshot": None,
                    "cash_snapshot": None,
                }

                if not args.dry_run:
                    accounting_result = ctx.post_fill_accounting_service.apply_fill(
                        uow=uow,
                        fill=sor_fill,
                        now_utc=now_utc,
                    )
                    fill_payload["position_snapshot"] = _model_to_dict(
                        getattr(accounting_result, "position_snapshot", None)
                    )
                    fill_payload["cash_snapshot"] = _model_to_dict(
                        getattr(accounting_result, "cash_snapshot", None)
                    )
                    risk_snapshot = ctx.risk_snapshot_service.compute_snapshot(
                        run_id=tracked_order.run_id,
                        timestamp=now_utc,
                        position_snapshot=getattr(accounting_result, "position_snapshot", None),
                        cash_snapshot=getattr(accounting_result, "cash_snapshot", None),
                    )
                    uow.risk_snapshots.upsert(risk_snapshot)
            else:
                fill_payload = None

        audit_event_id = None
        if not args.dry_run:
            audit_event_id = _record_execution_cli_audit_event(
                session,
                action="execution_reconcile_order",
                run_id=str(tracked_order.run_id),
                message=f"execution reconcile-order for order {args.order_id}",
                metadata={
                    "order_id": args.order_id,
                    "broker_order_id": getattr(tracked_order, "broker_order_id", None),
                    "dry_run": False,
                },
                occurred_at=now_utc,
            )

        _emit_json_result(
            args,
            {
                "status": "ok",
                "dry_run": args.dry_run,
                "order_id": args.order_id,
                "audit_event_id": audit_event_id,
                "broker_order": _model_to_dict(getattr(result, "broker_order", None)),
                "fill_result": fill_payload,
                "risk_snapshot": _model_to_dict(risk_snapshot)
                if risk_snapshot is not None
                else None,
            },
        )
        return 0
    finally:
        session.close()


def handle_reconcile_open_orders(args: argparse.Namespace) -> int:
    print_header("Reconcile Open Orders")
    run_id = _parse_uuid(args.run_id, field_name="run_id")
    now_utc = datetime.now(UTC)

    if args.dry_run:
        session = get_session()
        try:
            with SorUnitOfWork(session) as uow:
                orders = uow.tracked_orders.list_open_orders_for_run(run_id)
            _emit_json_result(
                args,
                {
                    "status": "ok",
                    "dry_run": True,
                    "run_id": str(run_id),
                    "open_order_count": len(orders),
                    "orders": [_model_to_dict(order) for order in orders],
                    "reconciled_at": None,
                },
            )
            return 0
        finally:
            session.close()

    settings = Settings()
    _assert_broker_cli_safety(settings, args)
    run_order_reconciliation_job(run_id=str(run_id), now_utc=now_utc)
    session = get_session()
    try:
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_reconcile_open_orders",
            run_id=str(run_id),
            message=f"execution reconcile-open-orders for run {run_id}",
            metadata={"run_id": str(run_id), "dry_run": False},
            occurred_at=now_utc,
        )
    finally:
        session.close()
    _emit_json_result(
        args,
        {
            "status": "ok",
            "dry_run": False,
            "run_id": str(run_id),
            "audit_event_id": audit_event_id,
            "reconciled_at": now_utc.isoformat(),
        },
    )
    return 0


def handle_broker_health(args: argparse.Namespace) -> int:
    print_header("Broker Health")
    settings = Settings()
    _assert_broker_cli_safety(settings, args)
    client = _build_broker_client(settings)
    result = BrokerStartupHealthCheckService().assert_broker_ready(client)
    payload = {
        "status": "ok",
        "broker": "alpaca",
        "trading_environment": settings.trading_environment.value,
        "account_id": result.account_id,
        "ready": result.ok,
    }
    _emit_json_result(args, payload)
    return 0


def handle_sync_broker_state(args: argparse.Namespace) -> int:
    print_header("Sync Broker State")
    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        run_id = (
            _parse_uuid(args.run_id, field_name="run_id") if getattr(args, "run_id", None) else None
        )
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        result = service.sync_broker_runtime_state(run_id=run_id, observed_at=now_utc)
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_sync_broker_state",
            run_id=str(run_id) if run_id else None,
            message="execution sync-broker-state",
            metadata={
                "run_id": str(run_id) if run_id else None,
                "broker_account_id": result.broker_account_id,
            },
            occurred_at=now_utc,
        )
        payload = {
            "status": "ok",
            "audit_event_id": audit_event_id,
            "sync": _runtime_sync_result_to_dict(result),
        }
        _emit_json_result(args, payload)
        return 0
    finally:
        session.close()


def handle_validate_broker_consistency(args: argparse.Namespace) -> int:
    print_header("Validate Broker Consistency")
    session = get_session()
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        result = service.validate_broker_runtime_consistency(tolerance=Decimal(str(args.tolerance)))
        payload = {
            "status": "ok" if result.ok else "drifted",
            "consistency": _runtime_sync_result_to_dict(result),
        }
        _emit_json_result(args, payload)
        return 0 if result.ok else 2
    finally:
        session.close()


def handle_sync_position_snapshot(args: argparse.Namespace) -> int:
    print_header("Sync Position Snapshot")
    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        run_id = _parse_uuid(args.run_id, field_name="run_id")
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        snapshot = service.sync_positions_from_broker(run_id=run_id, observed_at=now_utc)
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_sync_position_snapshot",
            run_id=str(run_id),
            message=f"execution sync-position-snapshot for run {run_id}",
            metadata={
                "run_id": str(run_id),
                "snapshot_id": str(snapshot.snapshot_id),
                "position_count": len(snapshot.positions or []),
            },
            occurred_at=now_utc,
        )
        payload = {
            "status": "ok",
            "audit_event_id": audit_event_id,
            "position_snapshot": _model_to_dict(snapshot),
            "positions": [_model_to_dict(item) for item in (snapshot.positions or [])],
        }
        _emit_json_result(args, payload)
        return 0
    finally:
        session.close()


def handle_sync_open_orders(args: argparse.Namespace) -> int:
    print_header("Sync Open Orders")
    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        run_id = _parse_uuid(args.run_id, field_name="run_id")
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        broker_order_ids = service.sync_open_orders_from_broker(
            run_id=run_id,
            account_id=args.account_id,
        )
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_sync_open_orders",
            run_id=str(run_id),
            message=f"execution sync-open-orders for run {run_id}",
            metadata={
                "run_id": str(run_id),
                "account_id": args.account_id,
                "broker_order_ids": list(broker_order_ids),
            },
            occurred_at=now_utc,
        )
        payload = {
            "status": "ok",
            "audit_event_id": audit_event_id,
            "run_id": str(run_id),
            "broker_order_ids": list(broker_order_ids),
            "synced_order_count": len(broker_order_ids),
        }
        _emit_json_result(args, payload)
        return 0
    finally:
        session.close()


def handle_sync_order_status(args: argparse.Namespace) -> int:
    print_header("Sync Order Status")
    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        try:
            broker_order_id = _parse_alpaca_broker_order_id(args.order_id)
        except ValueError as exc:
            _emit_json_result(
                args,
                {
                    "status": "invalid_argument",
                    "field": "order_id",
                    "value": args.order_id,
                    "message": str(exc),
                },
            )
            return 2
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        run_id = _parse_uuid(args.run_id, field_name="run_id")
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        synced_broker_order_id = service.sync_order_status(
            order_id=broker_order_id,
            run_id=run_id,
            account_id=args.account_id,
        )
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_sync_order_status",
            run_id=str(run_id),
            message=f"execution sync-order-status for broker order {broker_order_id}",
            metadata={
                "run_id": str(run_id),
                "account_id": args.account_id,
                "broker_order_id": synced_broker_order_id,
            },
            occurred_at=now_utc,
        )
        payload = {
            "status": "ok",
            "audit_event_id": audit_event_id,
            "run_id": str(run_id),
            "broker_order_id": synced_broker_order_id,
        }
        _emit_json_result(args, payload)
        return 0
    finally:
        session.close()


def handle_reconcile_order_fills(args: argparse.Namespace) -> int:
    print_header("Reconcile Order Fills")
    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        try:
            broker_order_id = _parse_alpaca_broker_order_id(args.order_id)
        except ValueError as exc:
            _emit_json_result(
                args,
                {
                    "status": "invalid_argument",
                    "field": "order_id",
                    "value": args.order_id,
                    "message": str(exc),
                },
            )
            return 2
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        run_id = _parse_uuid(args.run_id, field_name="run_id")
        service = _build_broker_runtime_sync_service(session=session, settings=settings)
        fill_ids = service.reconcile_order_fills(
            order_id=broker_order_id,
            run_id=run_id,
            account_id=args.account_id,
        )
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_reconcile_order_fills",
            run_id=str(run_id),
            message=f"execution reconcile-order-fills for broker order {broker_order_id}",
            metadata={
                "run_id": str(run_id),
                "account_id": args.account_id,
                "broker_order_id": broker_order_id,
                "fill_ids": list(fill_ids),
            },
            occurred_at=now_utc,
        )
        payload = {
            "status": "ok",
            "audit_event_id": audit_event_id,
            "run_id": str(run_id),
            "broker_order_id": broker_order_id,
            "fill_ids": list(fill_ids),
            "fill_count": len(fill_ids),
        }
        _emit_json_result(args, payload)
        return 0
    finally:
        session.close()


def handle_external_reconcile(args: argparse.Namespace) -> int:
    print_header("External Reconcile")
    session = get_session()
    try:
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        client = _build_broker_client(settings)
        service = ExternalBrokerReconciliationService(
            broker_client=client,
            session=session,
            environment=settings.trading_environment.value,
        )
        report = service.reconcile(args.run_id)
        audit_event_id = None
        if args.persist:
            with SorUnitOfWork(session) as uow:
                uow.reconciliation_snapshots.append_report(report)
            audit_event_id = _record_execution_cli_audit_event(
                session,
                action="execution_external_reconcile_persist",
                run_id=args.run_id,
                message=f"execution external-reconcile persisted report {report.report_id}",
                metadata={
                    "run_id": args.run_id,
                    "report_id": str(report.report_id),
                    "overall_status": report.overall_status.value,
                    "check_count": len(report.checks),
                },
                occurred_at=report.reconciled_at,
            )
        payload = {
            "status": report.overall_status.value,
            "persisted": args.persist,
            "audit_event_id": audit_event_id,
            "report": _report_to_dict(report),
        }
        _emit_json_result(args, payload)
        return 0 if report.overall_status.value == "passed" else 2
    finally:
        session.close()


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


def handle_inspect_fill_quality(args: argparse.Namespace) -> int:
    print_header("Inspect Fill Quality")
    session = get_session()
    try:
        record = FillQualityMetricsRepository(session).get_by_intent_id(args.intent_id)
        if record is None:
            _emit_json_result(
                args,
                {
                    "status": "not_found",
                    "intent_id": args.intent_id,
                    "message": "fill-quality metrics not found for intent",
                },
            )
            return 1
        _emit_json_result(
            args,
            {
                "status": "ok",
                "intent_id": args.intent_id,
                "fill_quality": _model_to_dict(record),
            },
        )
        return 0
    finally:
        session.close()


def handle_list_fill_quality(args: argparse.Namespace) -> int:
    print_header("List Fill Quality")
    session = get_session()
    try:
        limit = max(1, min(int(args.limit), 500))
        stmt = (
            select(FillQualityMetrics)
            .where(FillQualityMetrics.run_id == args.run_id)
            .order_by(desc(FillQualityMetrics.fill_timestamp))
            .limit(limit)
        )
        if args.adverse_only:
            stmt = stmt.where(FillQualityMetrics.is_adverse_fill.is_(True))
        records = list(session.scalars(stmt).all())
        _emit_json_result(
            args,
            {
                "status": "ok",
                "run_id": args.run_id,
                "adverse_only": args.adverse_only,
                "limit": limit,
                "count": len(records),
                "fill_quality": [_model_to_dict(record) for record in records],
            },
        )
        return 0
    finally:
        session.close()


def handle_inspect_runtime_state(args: argparse.Namespace) -> int:
    print_header("Inspect Runtime State")
    session = get_session()
    try:
        with SorUnitOfWork(session) as uow:
            state = uow.strategy_runtime_states.get_by_strategy_id(args.strategy_id)
        if state is None:
            _emit_json_result(
                args,
                {
                    "status": "not_found",
                    "strategy_id": args.strategy_id,
                    "message": "strategy runtime state not found",
                },
            )
            return 1
        _emit_json_result(
            args,
            {
                "status": "ok",
                "strategy_id": args.strategy_id,
                "strategy_runtime_state": _model_to_dict(state),
            },
        )
        return 0
    finally:
        session.close()


def handle_list_open_orders(args: argparse.Namespace) -> int:
    print_header("List Open Orders")
    session = get_session()
    try:
        platform_orders = []
        broker_orders = []
        if args.source in {"platform", "both"}:
            with SorUnitOfWork(session) as uow:
                platform_orders = uow.tracked_orders.list_open_orders()
        if args.source in {"broker", "both"}:
            settings = Settings()
            _assert_broker_cli_safety(settings, args)
            client = _build_broker_client(settings)
            broker_orders = client.list_open_orders()
        _emit_json_result(
            args,
            {
                "status": "ok",
                "source": args.source,
                "platform_open_order_count": len(platform_orders),
                "broker_open_order_count": len(broker_orders),
                "platform_orders": [_model_to_dict(order) for order in platform_orders],
                "broker_orders": _serialize_value(broker_orders),
            },
        )
        return 0
    finally:
        session.close()


def handle_cancel_order(args: argparse.Namespace) -> int:
    print_header("Cancel Order")
    if args.dry_run:
        _emit_json_result(
            args,
            {
                "status": "ok",
                "dry_run": True,
                "broker_order_id": args.broker_order_id,
                "message": "broker cancellation request was not sent",
            },
        )
        return 0
    if not args.confirm_cancel:
        _emit_json_result(
            args,
            {
                "status": "blocked",
                "broker_order_id": args.broker_order_id,
                "message": "re-run with --confirm-cancel to send the broker cancellation request",
            },
        )
        return 2

    session = get_session()
    now_utc = datetime.now(UTC)
    try:
        try:
            broker_order_id = _parse_alpaca_broker_order_id(args.broker_order_id)
        except ValueError as exc:
            _emit_json_result(
                args,
                {
                    "status": "invalid_argument",
                    "field": "broker_order_id",
                    "value": args.broker_order_id,
                    "message": str(exc),
                },
            )
            return 2
        settings = Settings()
        _assert_broker_cli_safety(settings, args)
        deps = build_cli_execution_dependencies(session)
        deps.execution_context.order_execution_service.cancel_order(broker_order_id)
        audit_event_id = _record_execution_cli_audit_event(
            session,
            action="execution_cancel_order",
            run_id=None,
            message=f"execution cancel-order for broker order {broker_order_id}",
            metadata={"broker_order_id": broker_order_id},
            occurred_at=now_utc,
        )
        _emit_json_result(
            args,
            {
                "status": "ok",
                "dry_run": False,
                "broker_order_id": broker_order_id,
                "audit_event_id": audit_event_id,
            },
        )
        return 0
    finally:
        session.close()


def handle_stream_orders(args: argparse.Namespace) -> int:
    print_header("Stream Orders")
    duration_seconds = max(1, min(int(args.duration_seconds), 600))
    if args.dry_run:
        _emit_json_result(
            args,
            {
                "status": "ok",
                "dry_run": True,
                "duration_seconds": duration_seconds,
                "message": "broker stream was not opened",
            },
        )
        return 0

    settings = Settings()
    _assert_broker_cli_safety(settings, args)
    if not settings.broker_api_key or not settings.broker_api_secret:
        raise RuntimeError("Broker stream requires configured broker API credentials.")

    events: list[dict] = []

    async def _on_event(event: dict) -> None:
        events.append(event)

    async def _run_stream() -> None:
        stream_client = AlpacaOrderStreamClient(
            api_key=settings.broker_api_key or "",
            secret_key=settings.broker_api_secret or "",
            paper=settings.trading_environment is TradingEnvironment.PAPER,
        )
        service = BrokerEventStreamService(stream_client=stream_client, on_event=_on_event)
        task = asyncio.create_task(service.start())
        try:
            await asyncio.sleep(duration_seconds)
        finally:
            service.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(_run_stream())
    _emit_json_result(
        args,
        {
            "status": "ok",
            "dry_run": False,
            "duration_seconds": duration_seconds,
            "event_count": len(events),
            "events": _serialize_value(events[-10:]),
        },
    )
    return 0


def handle_submit_intents(args: argparse.Namespace) -> int:
    print_header("Submit Intents")
    if not args.dry_run:
        _emit_json_result(
            args,
            {
                "status": "blocked",
                "run_id": args.run_id,
                "message": "direct CLI submission is not enabled; use the scheduler job path",
            },
        )
        return 2

    session = get_session()
    try:
        run_id = _parse_uuid(args.run_id, field_name="run_id")
        stmt = select(OrderIntents).where(OrderIntents.run_id == run_id)
        intents = list(session.scalars(stmt).all())
        _emit_json_result(
            args,
            {
                "status": "ok",
                "dry_run": True,
                "run_id": str(run_id),
                "intent_count": len(intents),
                "intents": [_model_to_dict(intent) for intent in intents],
                "message": "no broker submissions were sent",
            },
        )
        return 0
    finally:
        session.close()


def handle_policy_preview(args: argparse.Namespace) -> int:
    print_header("Policy Preview")
    session = get_session()
    try:
        intent_id = _parse_uuid(args.intent_id, field_name="intent_id")
        with SorUnitOfWork(session) as uow:
            row = uow.order_intents.get_by_intent_id(str(intent_id))
        if row is None:
            _emit_json_result(
                args,
                {
                    "status": "not_found",
                    "intent_id": args.intent_id,
                    "message": "order intent not found",
                },
            )
            return 1

        intent = _order_intent_row_to_contract(row)
        policy_config = ExecutionPolicyConfig(policy_mode=PolicyMode(args.policy_mode))
        if args.offline:
            if args.mid_price is None:
                raise ValueError("--mid-price is required with --offline")
            broker_client = cast(
                AlpacaBrokerClient, _StaticQuoteBrokerClient(mid_price=Decimal(str(args.mid_price)))
            )
            engine = ExecutionPolicyEngine(broker_client=broker_client)
        else:
            settings = Settings()
            _assert_broker_cli_safety(settings, args)
            deps = build_cli_execution_dependencies(session)
            engine = deps.execution_context.execution_policy_engine

        result = engine.apply(intent=intent, config=policy_config)
        _emit_json_result(
            args,
            {
                "status": "ok",
                "intent_id": args.intent_id,
                "policy_mode": args.policy_mode,
                "offline": args.offline,
                "transformed_intent": _serialize_value(
                    result.transformed_intent.model_dump(mode="json")
                ),
                "reference_price": _serialize_value(result.reference_price),
                "expected_slippage": _serialize_value(result.expected_slippage),
                "expected_cost": _serialize_value(result.expected_cost),
                "slices": _serialize_value(result.slices),
                "policy_metadata": _serialize_value(result.policy_metadata),
            },
        )
        return 0
    finally:
        session.close()
