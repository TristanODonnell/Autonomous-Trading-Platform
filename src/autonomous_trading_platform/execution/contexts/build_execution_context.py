# autonomous_trading_platform/execution/contexts/build_execution_context.py

from decimal import Decimal

from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.contexts.execution_context import ExecutionContext
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.policy.execution_policy_engine import (
    ExecutionPolicyEngine,
)
from autonomous_trading_platform.execution.policy.order_type_resolver import OrderTypeResolver
from autonomous_trading_platform.execution.policy.twap_slicer import TWAPSlicer
from autonomous_trading_platform.execution.policy.vwap_lite_slicer import VWAPLiteSlicer
from autonomous_trading_platform.execution.services.broker_adaptor import AlpacaBrokerAdapter
from autonomous_trading_platform.execution.services.broker_startup_health_check_service import (
    BrokerStartupHealthCheckService,
)
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.order_execution_service import (
    OrderExecutionService,
)
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
    OrderReconciliationService,
)
from autonomous_trading_platform.execution.services.order_runtime_state_service import (
    OrderRuntimeStateService,
)
from autonomous_trading_platform.execution.services.order_state_machine_service import (
    OrderStateMachineService,
)
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.execution.services.position_sizer import PositionSizer
from autonomous_trading_platform.execution.services.post_fill_accounting_service import (
    PostFillAccountingService,
)
from autonomous_trading_platform.execution.services.realised_slippage_service import (
    RealisedSlippageService,
)
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskSnapshotService,
)
from autonomous_trading_platform.execution.services.strategy_runtime_state_service import (
    StrategyRuntimeStateService,
)
from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyStateMachineService,
)
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.storage.sor.repositories.core.fill_quality_metrics_repository import (
    FillQualityMetricsRepository,
)


def build_execution_context(
    *,
    session,
    pre_trade_risk_service,
    audit_log_repository,
    alpaca_settings,
    portfolio_engine: PortfolioEngine,
) -> ExecutionContext:
    broker_client = AlpacaBrokerClient(alpaca_settings)
    BrokerStartupHealthCheckService().assert_broker_ready(broker_client)
    broker_adapter = AlpacaBrokerAdapter()

    order_execution_service = OrderExecutionService(
        broker_client=broker_client,
        adapter=broker_adapter,
        settings=alpaca_settings,
    )

    order_state_machine_service = OrderStateMachineService(
        audit_logger=audit_log_repository,
    )

    strategy_state_machine_service = StrategyStateMachineService()

    position_sizer = PositionSizer(
        portfolio_engine=portfolio_engine,
        max_symbol_exposure_usd=Decimal(str(alpaca_settings.max_symbol_exposure)),
    )

    volatility_scaling_service = VolatilityScalingService()

    portfolio_construction_service = PortfolioConstructionService(
        pre_trade_risk_service=pre_trade_risk_service,
        position_sizer=position_sizer,
        volatility_scaling_service=volatility_scaling_service,
    )

    fill_quality_repo = FillQualityMetricsRepository(session)
    realised_slippage_service = RealisedSlippageService(repository=fill_quality_repo)

    execution_policy_engine = ExecutionPolicyEngine(
        broker_client=broker_client,
        order_type_resolver=OrderTypeResolver(),
        twap_slicer=TWAPSlicer(),
        vwap_lite_slicer=VWAPLiteSlicer(),
    )

    broker_order_mapper = BrokerOrderMapper()

    order_reconciliation_service = OrderReconciliationService(
        order_execution_service=order_execution_service,
        broker_order_mapper=broker_order_mapper,
        order_state_machine_service=order_state_machine_service,
    )

    order_runtime_state_service = OrderRuntimeStateService()
    strategy_runtime_state_service = StrategyRuntimeStateService(
        strategy_state_machine_service=strategy_state_machine_service
    )
    position_ledger_service = PositionLedgerService()
    cash_ledger_service = CashLedgerService()
    post_fill_accounting_service = PostFillAccountingService(
        position_ledger_service=position_ledger_service,
        cash_ledger_service=cash_ledger_service,
    )
    risk_snapshot_service = RiskSnapshotService()

    return ExecutionContext(
        broker_client=broker_client,
        broker_adapter=broker_adapter,
        order_execution_service=order_execution_service,
        order_state_machine_service=order_state_machine_service,
        strategy_state_machine_service=strategy_state_machine_service,
        portfolio_construction_service=portfolio_construction_service,
        realised_slippage_service=realised_slippage_service,
        broker_order_mapper=broker_order_mapper,
        order_reconciliation_service=order_reconciliation_service,
        order_runtime_state_service=order_runtime_state_service,
        strategy_runtime_state_service=strategy_runtime_state_service,
        post_fill_accounting_service=post_fill_accounting_service,
        risk_snapshot_service=risk_snapshot_service,
        execution_policy_engine=execution_policy_engine,
    )
