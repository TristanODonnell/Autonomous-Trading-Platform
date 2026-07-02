from dataclasses import dataclass, field

from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)
from autonomous_trading_platform.research.experiments.services.experiment_orchestration_service import (
    ExperimentOrchestrationService,
)
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)


@dataclass
class SimulationContext:
    bar_reader: HistoricalBarDatasetReader
    dataset_resolver: ResearchDatasetResolver
    window_loader: SimulationWindowLoader
    parquet_simulation_repository: ParquetSimulationRepository
    result_recorder_service: ResultRecorderService
    simulation_runner: SimulationRunner
    simulation_engine: SimulationExecutionEngine
    experiment_orchestration_service: ExperimentOrchestrationService
    simulation_result_cache: SimulationResultCache = field(default_factory=SimulationResultCache)
    strategy_generation_cache: StrategyGenerationCache = field(
        default_factory=StrategyGenerationCache
    )
