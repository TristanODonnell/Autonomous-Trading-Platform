from datetime import datetime

from autonomous_trading_platform.contracts.common.enums import StrategyEvent
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import EvaluateStrategyJob


def run_trading_evaluation_job(
    *,
    now_utc: datetime,
    trading_cycle_dependencies: TradingCycleDependencies,
    manifest: RunManifest,
):
    strategy_context = trading_cycle_dependencies.strategy_context
    execution_context = trading_cycle_dependencies.execution_context
    session = trading_cycle_dependencies.session

    evaluate_strategy_job = EvaluateStrategyJob(
        readiness_service=strategy_context.strategy_bar_readiness_service,
        evaluation_service=strategy_context.strategy_evaluation_service,
        signal_writer=strategy_context.signal_writer,
        checkpoint_writer=strategy_context.strategy_checkpoint_writer,
    )

    strategy_job_result = evaluate_strategy_job.run(now_utc)

    if strategy_job_result.signals:
        with SorUnitOfWork(session) as uow:
            execution_context.strategy_runtime_state_service.apply_event(
                uow=uow,
                strategy_id=manifest.strategy_id,
                event=StrategyEvent.SIGNAL_GENERATED,
                now_utc=now_utc,
            )
    if strategy_job_result.target_bar_timestamp is None:
        raise ValueError("target_bar_timestamp is required for order intent generation")

    generated_intents = execution_context.portfolio_construction_service.generate_order_intents(
        signals=strategy_job_result.signals,
        positions={},
        prices={"SPY": 500.0},
        run_id=manifest.run_id,
        strategy_id=manifest.strategy_id,
        bar_timestamp=strategy_job_result.target_bar_timestamp,
        now=now_utc,
    )

    return strategy_job_result, generated_intents
