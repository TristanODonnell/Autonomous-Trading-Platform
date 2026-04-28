from dataclasses import dataclass

from autonomous_trading_platform.runtime.services.run_manifest_service import (
    RunManifestService,
)
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import SignalWriter
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    StrategyBarReadinessService,
)
from autonomous_trading_platform.strategy.services.strategy_checkpoint_writer_service import (
    StrategyCheckpointWriter,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)


@dataclass
class StrategyRuntimeContext:
    strategy_evaluation_service: StrategyEvaluationService
    strategy_bar_readiness_service: StrategyBarReadinessService
    signal_writer: SignalWriter
    strategy_checkpoint_writer: StrategyCheckpointWriter
    run_manifest_service: RunManifestService
