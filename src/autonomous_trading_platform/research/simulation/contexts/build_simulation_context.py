from sqlalchemy.orm import Session

from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.contexts.simulation_context import (
    SimulationContext,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)


def build_simulation_context(*, session: Session) -> SimulationContext:
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

    simulation_runner = SimulationRunner(
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        result_recorder=result_recorder_service,
    )

    return SimulationContext(
        bar_reader=bar_reader,
        dataset_resolver=dataset_resolver,
        window_loader=window_loader,
        parquet_simulation_repository=parquet_simulation_repository,
        result_recorder_service=result_recorder_service,
        simulation_runner=simulation_runner,
    )
