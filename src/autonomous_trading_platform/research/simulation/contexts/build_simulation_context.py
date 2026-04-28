from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.contexts.simulation_context import (
    SimulationContext,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory


def build_simulation_context(*, session: Session) -> SimulationContext:
    settings = Settings()
    strategy_factory = StrategyFactory()

    bar_reader = HistoricalBarDatasetReader(
        session=session,
        base_path="data",
    )

    dataset_resolver = ResearchDatasetResolver(base_path="data")

    window_loader = SimulationWindowLoader(
        bar_reader=bar_reader,
    )

    parquet_simulation_repository = ParquetSimulationRepository()

    result_recorder_service = ResultRecorderService(
        parquet_simulation_repository=parquet_simulation_repository,
    )
    pre_trade_risk_service = PreTradeRiskService(
        settings=settings,
        risk_state_reader=StubRiskStateReader(),
    )
    context_builder = StrategyContextBuilder(
        market_bar_reader=bar_reader,
        bars_dataset=ADJUSTED_BARS_DATASET,  # or RAW_BARS_DATASET
        lookback_bars=20,
    )
    portfolio_construction_service = PortfolioConstructionService(
        pre_trade_risk_service=pre_trade_risk_service
    )
    simulated_execution_service = SimulatedExecutionService()
    cash_ledger_service = CashLedgerService()
    position_ledger_service = PositionLedgerService()
    simulation_engine = SimulationExecutionEngine(
        cash_ledger_service=cash_ledger_service,
        position_ledger_service=position_ledger_service,
    )
    simulation_runner = SimulationRunner(
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        result_recorder=result_recorder_service,
        execution_engine=simulation_engine,
        context_builder=context_builder,
        portfolio_construction_service=portfolio_construction_service,
        simulated_execution_service=simulated_execution_service,
        strategy_factory=strategy_factory,
    )

    return SimulationContext(
        bar_reader=bar_reader,
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        parquet_simulation_repository=parquet_simulation_repository,
        result_recorder_service=result_recorder_service,
        simulation_runner=simulation_runner,
        simulation_engine=simulation_engine,
    )
