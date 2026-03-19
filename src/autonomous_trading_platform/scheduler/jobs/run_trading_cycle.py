import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    OrderEvent,
    OrderStatus,
    RunType,
    StrategyEvent,
    StrategyState,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    build_execution_context,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.safety.contexts.build_safety_context import build_safety_context
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import StubOrderActivityReader
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.contexts.build_strategy_context import (
    build_strategy_context,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import (
    EvaluateStrategyJob,
)
from src.db import get_session


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def run_trading_cycle():
    """
    Entry point for airflow DAG.
    """

    settings = Settings()
    environment_safety_policy = EnvironmentSafetyPolicy(settings=settings)
    session: Session = get_session()
    audit_logger = AuditLoggingService(session)
    manifest_service = RunManifestService(session)

    strategy_stub = StubStrategy()
    strategy_context = build_strategy_context(strategy=strategy_stub)
    risk_state_reader = StubRiskStateReader()
    order_activity_reader = StubOrderActivityReader()

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_safety_policy,
        risk_state_reader=risk_state_reader,
        order_activity_reader=order_activity_reader,
    )

    execution_context = build_execution_context(
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_logger,
        alpaca_settings=settings,
    )

    run_id = uuid.uuid4()
    component = "scheduler.run_trading_cycle"
    expected_symbols = {"SPY"}
    now_utc = datetime.now(UTC)
    cycle_end = floor_to_five_minutes(now_utc)
    cycle_start = cycle_end - timedelta(minutes=5)

    manifest = RunManifest(
        run_id=run_id,
        run_type=RunType.BACKTEST,
        created_at=now_utc,
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id="baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.FIVE_MIN,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version="v1",
        git_commit="dev",
        python_version=platform.python_version(),
        notes="5-minute trading cycle",
    )
    manifest_service.save(manifest)
    base_metadata = {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "expected_symbols": sorted(expected_symbols),
        "manifest_run_type": manifest.run_type.value,
        "manifest_interval": manifest.interval.value,
    }

    try:
        audit_logger.record_run_started(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )

        safety_context.shadow_mode_service.is_enabled()
        # only check live gate if actually in LIVE mode
        if settings.trading_environment is TradingEnvironment.LIVE:
            safety_context.live_trading_gate_service.assert_live_trading_allowed(
                manifest.broker_account_id
            )

        evaluate_strategy_job = EvaluateStrategyJob(
            readiness_service=strategy_context.readiness_service,
            evaluation_service=strategy_context.strategy_evaluation_service,
            signal_writer=strategy_context.signal_writer,
            checkpoint_writer=strategy_context.strategy_evaluation_checkpoint_reader_protocol,
        )

        strategy_state = StrategyState.IDLE
        order_intents_created = False
        shadow_mode_enabled = safety_context.shadow_mode_service.is_enabled()

        strategy_job_result = evaluate_strategy_job.run(now_utc)

        if strategy_job_result.signals:
            strategy_state = execution_context.strategy_state_machine_service.apply_event(
                strategy_id=manifest.strategy_id,
                current_state=strategy_state,
                event=StrategyEvent.SIGNAL_GENERATED,
            )

        generated_intents = execution_context.portfolio_construction_service.generate_order_intents(
            signals=strategy_job_result.signals,
            positions={},
            prices={"SPY": 500.0},
            run_id=run_id,
            strategy_id=manifest.strategy_id,
            bar_timestamp=strategy_job_result.target_bar_timestamp,
            now=now_utc,
        )

        for intent in generated_intents:
            if not order_intents_created:
                strategy_state = execution_context.strategy_state_machine_service.apply_event(
                    strategy_id=manifest.strategy_id,
                    current_state=strategy_state,
                    event=StrategyEvent.ORDER_INTENTS_CREATED,
                )
                order_intents_created = True

            order_status = OrderStatus.NEW

            idempotency_key = (
                safety_context.order_idempotency_service.assert_not_duplicate_within_window(
                    intent,
                    now_utc,
                )
            )
            intent.idempotency_key = idempotency_key

            safety_context.order_throttle_service.assert_order_allowed_for_submission(
                order_intent=intent,
                now=now_utc,
                bar_timestamp=intent.bar_timestamp,
            )

            if shadow_mode_enabled:
                continue

            try:
                response = execution_context.order_execution_service.submit(intent)

                broker_order = execution_context.broker_order_mapper.to_broker_order(
                    payload=response,
                    intent_id=intent.intent_id,
                    run_id=run_id,
                    account_id=manifest.broker_account_id,
                )

                with SorUnitOfWork(session) as uow:
                    # also persist the intent here if not already persisted elsewhere
                    uow.broker_orders.upsert(broker_order)

                order_status = execution_context.order_state_machine_service.apply_event(
                    order_id=intent.order_id,
                    current_status=order_status,
                    event=OrderEvent.SUBMIT,
                    event_timestamp=now_utc,
                    run_id=str(run_id),
                    metadata={
                        "symbol": intent.symbol,
                        "strategy_id": intent.strategy_id,
                        "broker_order_id": broker_order.broker_order_id,
                        "broker_status": broker_order.status.value,
                    },
                )

            except Exception as exc:
                order_status = execution_context.order_state_machine_service.apply_event(
                    order_id=intent.order_id,
                    current_status=order_status,
                    event=OrderEvent.REJECT,
                    event_timestamp=now_utc,
                    run_id=str(run_id),
                    metadata={
                        "symbol": intent.symbol,
                        "strategy_id": intent.strategy_id,
                        "error": str(exc),
                    },
                )
                raise

        # -----------------------------
        # Reconciliation stage
        # -----------------------------
        tracked_orders = ...  # load submitted / partially filled tracked orders

        for tracked_order in tracked_orders:
            result = execution_context.order_reconciliation_service.reconcile_order(
                tracked_order,
                now=now_utc,
            )

            with SorUnitOfWork(session) as uow:
                uow.broker_orders.upsert(result.broker_order)

                if result.fill is not None:
                    uow.fills.upsert(result.fill)

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
