# autonomous_trading_platform/execution/contexts/execution_context.py
from dataclasses import dataclass

from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.policy.execution_policy_engine import (
    ExecutionPolicyEngine,
)
from autonomous_trading_platform.execution.services.broker_adaptor import AlpacaBrokerAdapter
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


@dataclass
class ExecutionContext:
    broker_client: AlpacaBrokerClient
    broker_adapter: AlpacaBrokerAdapter
    order_execution_service: OrderExecutionService
    order_state_machine_service: OrderStateMachineService
    strategy_state_machine_service: StrategyStateMachineService
    portfolio_construction_service: PortfolioConstructionService
    realised_slippage_service: RealisedSlippageService
    broker_order_mapper: BrokerOrderMapper
    order_reconciliation_service: OrderReconciliationService
    order_runtime_state_service: OrderRuntimeStateService
    strategy_runtime_state_service: StrategyRuntimeStateService
    post_fill_accounting_service: PostFillAccountingService
    risk_snapshot_service: RiskSnapshotService
    execution_policy_engine: ExecutionPolicyEngine
