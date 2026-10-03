from sqlalchemy.orm import Session

from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    build_platform_execution_service,
)
from autonomous_trading_platform.execution.services.cash_ledger_service import CashLedgerService
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerService,
)
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreService,
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
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simple_position_sizer import (
    SimplePositionSizer,
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
from autonomous_trading_platform.storage.parquet.datasets import ADJUSTED_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.experiments_repository import (
    ExperimentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.metrics_summary_repository import (
    MetricsSummaryRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.simulation_runs_repository import (
    SimulationRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_configs_repository import (
    StrategyConfigsRepository,
)
from autonomous_trading_platform.storage.sor.services.corporate_action_split_source import (
    SorCorporateActionSource,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory

_DEFAULT_UNIVERSE_SIZE = 5
_DEFAULT_TOTAL_CAPITAL = 100_000.00


def build_simulation_context(
    *, session: Session, universe_size: int | None = None
) -> SimulationContext:
    strategy_factory = StrategyFactory()

    bar_reader = HistoricalBarDatasetReader(session=session, base_path="data")
    dataset_resolver = ResearchDatasetResolver(base_path="data")
    window_loader = SimulationWindowLoader(bar_reader=bar_reader, feature_reader=bar_reader)

    parquet_simulation_repository = ParquetSimulationRepository()
    simulation_runs_repository = SimulationRunsRepository(session=session)
    strategy_configs_repository = StrategyConfigsRepository(session=session)
    result_recorder_service = ResultRecorderService(
        parquet_simulation_repository=parquet_simulation_repository
    )

    lookahead_guard_service = LookaheadGuardService()

    context_builder = StrategyContextBuilder(
        market_bar_reader=bar_reader,
        bars_dataset=ADJUSTED_BARS_DATASET,
        # SimulationRunner hands each strategy its registry warmup in bars
        # (StrategyDefinition.context_lookback_bars), as the trading cycle does.
        lookahead_guard_service=lookahead_guard_service,
    )

    # Simulation sizing: pure math, no DB, no governance, no policies. Same rule as the
    # platform portfolio cycle (execution/services/sleeve_sizing.py): equal split across
    # the universe, scaled down by the same volatility scalar.
    position_sizer = SimplePositionSizer(
        total_capital=_DEFAULT_TOTAL_CAPITAL,
        universe_size=universe_size if universe_size is not None else _DEFAULT_UNIVERSE_SIZE,
        volatility_scaling_service=VolatilityScalingService(),
    )

    # The platform's fill model (current-bar close, 5% volume participation cap,
    # volume-share slippage, zero commission), so re-sims fill as the platform does.
    simulated_execution_service = build_platform_execution_service()

    simulation_engine = SimulationExecutionEngine(
        cash_ledger_service=CashLedgerService(),
        position_ledger_service=PositionLedgerService(),
        lookahead_guard_service=lookahead_guard_service,
        position_sizer=position_sizer,
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
        # Stored splits and cash dividends are applied in every research run (plan 5d-E).
        corporate_action_source=SorCorporateActionSource(session),
    )

    filter_score_service = FilterScoreService(
        filter_config=FilterConfig(),
        scoring_weights=ScoringWeights(),
    )
    experiment_orchestration_service = ExperimentOrchestrationService(
        experiment_repository=experiments_repository,
        simulation_runner=simulation_runner,
        strategy_generation_engine=StrategyGenerationEngine(generator=GridSearchGenerator()),
        filter_score_service=filter_score_service,
    )

    simulation_result_cache = SimulationResultCache()
    strategy_generation_cache = StrategyGenerationCache()

    return SimulationContext(
        bar_reader=bar_reader,
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        parquet_simulation_repository=parquet_simulation_repository,
        result_recorder_service=result_recorder_service,
        simulation_runner=simulation_runner,
        simulation_engine=simulation_engine,
        experiment_orchestration_service=experiment_orchestration_service,
        simulation_result_cache=simulation_result_cache,
        strategy_generation_cache=strategy_generation_cache,
    )
