from dataclasses import dataclass

from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import (
    SignalWriterProtocol,
    StrategyCheckpointWriterProtocol,
)
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    IngestionStatusReaderProtocol,
    StrategyBarReadinessService,
    StrategyEvaluationCheckpointReaderProtocol,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    MarketBarReaderProtocol,
    StrategyEvaluationService,
    UniverseMembershipReaderProtocol,
)


@dataclass
class StrategyContext:
    market_bar_reader_protocol: MarketBarReaderProtocol
    universe_membership_reader_protocol: UniverseMembershipReaderProtocol

    ingestion_status_reader_protocol: IngestionStatusReaderProtocol
    strategy_evaluation_checkpoint_reader_protocol: StrategyEvaluationCheckpointReaderProtocol

    strategy_evaluation_service: StrategyEvaluationService
    strategy_bar_readiness_service: StrategyBarReadinessService

    signal_writer: SignalWriterProtocol
    strategy_checkpoint_writer: StrategyCheckpointWriterProtocol
