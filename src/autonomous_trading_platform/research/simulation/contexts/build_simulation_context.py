from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.research.experiments.services.experiment_orchestration_service import (
    ExperimentOrchestrationService,
)
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.contexts.simulation_context import (
    SimulationContext,
)
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.order_simulator_service import (
    OrderSimulatorService,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.research.strategy_generation.generators.grid_search_generator import (
    GridSearchGenerator,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.experiments_repository import (
    ExperimentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.metrics_summary_repository import (
    MetricsSummaryRepository,
)
from autonomous_trading_platform.storage.sor.repositories.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.repositories.simulation_runs_repository import (
    SimulationRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.strategy_configs_repository import (
    StrategyConfigsRepository,
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
        feature_reader=bar_reader,
    )

    parquet_simulation_repository = ParquetSimulationRepository()
    simulation_runs_repository = SimulationRunsRepository(session=session)
    strategy_configs_repository = StrategyConfigsRepository(session=session)
    result_recorder_service = ResultRecorderService(
        parquet_simulation_repository=parquet_simulation_repository,
    )
    pre_trade_risk_service = PreTradeRiskService(
        settings=settings,
        risk_state_reader=StubRiskStateReader(),
    )
    lookahead_guard_service = LookaheadGuardService()

    context_builder = StrategyContextBuilder(
        market_bar_reader=bar_reader,
        bars_dataset=ADJUSTED_BARS_DATASET,
        lookback_bars=20,
        lookahead_guard_service=lookahead_guard_service,
    )
    portfolio_construction_service = PortfolioConstructionService(
        pre_trade_risk_service=pre_trade_risk_service
    )

    # TODO Later should move into settings
    simulation_cost_model_config = SimulationCostModelConfig(
        commission_per_share=Decimal("0.0000"),
        min_commission=Decimal("0.00"),
    )
    slippage_model_config = SlippageModelConfig(
        slippage_rate=Decimal("0.0001")  # or from settings
    )
    slippage_model = SlippageModel(config=slippage_model_config)
    simulation_cost_model_service = SimulationCostModelService(
        config=simulation_cost_model_config,
        slippage_model=slippage_model,
    )
    fill_model_config = SimulatedFillModelConfig()
    simulated_execution_service = SimulatedExecutionService(
        simulation_cost_model_service=simulation_cost_model_service,
        fill_model_config=fill_model_config,
    )
    cash_ledger_service = CashLedgerService()
    position_ledger_service = PositionLedgerService()
    order_simulator_service = OrderSimulatorService(
        portfolio_construction_service=portfolio_construction_service,
    )
    simulation_engine = SimulationExecutionEngine(
        cash_ledger_service=cash_ledger_service,
        position_ledger_service=position_ledger_service,
        lookahead_guard_service=lookahead_guard_service,
        order_simulator_service=order_simulator_service,
    )
    experiments_repository = ExperimentsRepository(session=session)
    metrics_summary_repository = MetricsSummaryRepository(session=session)
    simulation_runner = SimulationRunner(
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        result_recorder=result_recorder_service,
        execution_engine=simulation_engine,
        context_builder=context_builder,
        simulated_execution_service=simulated_execution_service,
        strategy_factory=strategy_factory,
        strategy_config_repository=strategy_configs_repository,
        simulation_run_repository=simulation_runs_repository,
        manifest_service=RunManifestRepository(session),
        experiment_repository=experiments_repository,
        metrics_summary_repository=metrics_summary_repository,
    )
    strategy_generation_engine = StrategyGenerationEngine(
        generator=GridSearchGenerator(),  # Set as default hardcoded for now
    )

    experiment_orchestration_service = ExperimentOrchestrationService(
        experiment_repository=experiments_repository,
        simulation_runner=simulation_runner,
        strategy_generation_engine=strategy_generation_engine,
    )

    return SimulationContext(
        bar_reader=bar_reader,
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        parquet_simulation_repository=parquet_simulation_repository,
        result_recorder_service=result_recorder_service,
        simulation_runner=simulation_runner,
        simulation_engine=simulation_engine,
        experiment_orchestration_service=experiment_orchestration_service,
    )
