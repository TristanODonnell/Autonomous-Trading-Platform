from autonomous_trading_platform.strategy.contexts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
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


def build_strategy_context(
    *,
    strategy: BaseStrategy,
):
    market_bar_reader_protocol = MarketBarReaderProtocol()
    universe_membership_reader_protocol = UniverseMembershipReaderProtocol()
    ingestion_status_reader_protocol = IngestionStatusReaderProtocol()
    strategy_evaluation_checkpoint_reader_protocol = StrategyEvaluationCheckpointReaderProtocol()
    signal_writer_protocol = SignalWriterProtocol()
    strategy_checkpoint_writer_protocol = StrategyCheckpointWriterProtocol()
    strategy_evaluation_service = StrategyEvaluationService(
        market_bar_reader=market_bar_reader_protocol,
        universe_reader=universe_membership_reader_protocol,
        strategy=strategy,
    )
    strategy_bar_readiness_service = StrategyBarReadinessService(
        ingestion_status_reader=ingestion_status_reader_protocol,
        checkpoint_reader=strategy_evaluation_checkpoint_reader_protocol,
    )

    return StrategyContext(
        market_bar_reader_protocol=market_bar_reader_protocol,
        universe_membership_reader_protocol=universe_membership_reader_protocol,
        ingestion_status_reader_protocol=ingestion_status_reader_protocol,
        strategy_evaluation_checkpoint_reader_protocol=strategy_evaluation_checkpoint_reader_protocol,
        strategy_bar_readiness_service=strategy_bar_readiness_service,
        strategy_evaluation_service=strategy_evaluation_service,
        strategy_checkpoint_writer=strategy_checkpoint_writer_protocol,
        signal_writer=signal_writer_protocol,
    )
