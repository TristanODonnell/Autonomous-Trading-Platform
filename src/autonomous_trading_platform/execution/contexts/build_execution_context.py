from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.contexts.execution_context import ExecutionContext
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.services.broker_adaptor import AlpacaBrokerAdapter
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
from autonomous_trading_platform.execution.services.post_fill_accounting_service import (
    PostFillAccountingService,
)
from autonomous_trading_platform.execution.services.realised_slippage_service import (
    RealisedSlippageService,
)
from autonomous_trading_platform.execution.services.strategy_runtime_state_service import (
    StrategyRuntimeStateService,
)
from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyStateMachineService,
)


def build_execution_context(
    *,
    pre_trade_risk_service,
    audit_log_repository,
    alpaca_settings,
) -> ExecutionContext:
    broker_client = AlpacaBrokerClient(alpaca_settings)
    broker_adapter = AlpacaBrokerAdapter()

    order_execution_service = OrderExecutionService(
        broker_client=broker_client,
        adapter=broker_adapter,
    )

    order_state_machine_service = OrderStateMachineService(
        audit_logger=audit_log_repository,
    )

    strategy_state_machine_service = StrategyStateMachineService()

    portfolio_construction_service = PortfolioConstructionService(
        pre_trade_risk_service=pre_trade_risk_service,
    )

    realised_slippage_service = RealisedSlippageService()

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
    )
