from datetime import datetime
from time import perf_counter

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.contracts.common.enums import StrategyEvent
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    trading_evaluation_job_duration,
    trading_evaluation_job_failures,
    trading_evaluation_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import EvaluateStrategyJob

logger = get_logger(__name__)

TRADING_EVALUATION_JOB_METRICS = JobMetricSet(
    runs=trading_evaluation_job_runs,
    failures=trading_evaluation_job_failures,
    duration=trading_evaluation_job_duration,
)


def run_trading_evaluation_job(
    *,
    now_utc: datetime,
    trading_cycle_dependencies: TradingCycleDependencies,
    manifest: RunManifest,
):
    component = "scheduler.jobs.trading_evaluation_job"
    job = "trading_evaluation_job"
    job_start = perf_counter()

    strategy_context = trading_cycle_dependencies.strategy_context
    execution_context = trading_cycle_dependencies.execution_context
    session = trading_cycle_dependencies.session

    record_job_started(
        logger=logger,
        metrics=TRADING_EVALUATION_JOB_METRICS,
        job=job,
        component=component,
        run_id=str(manifest.run_id),
    )
    try:
        with start_span(
            "trading_evaluation_job.run",
            timespan=SpanTimespan.JOB,
        ) as job_span:
            job_span.set_attribute("ratp.run_id", manifest.run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", now_utc.isoformat())

            evaluate_strategy_job = EvaluateStrategyJob(
                readiness_service=strategy_context.strategy_bar_readiness_service,
                evaluation_service=strategy_context.strategy_evaluation_service,
                signal_writer=strategy_context.signal_writer,
                checkpoint_writer=strategy_context.strategy_checkpoint_writer,
                run_manifest_service=trading_cycle_dependencies.manifest_service,
            )

            strategy_job_result = evaluate_strategy_job.run(
                now=now_utc,
                parent_run_id=str(manifest.run_id),
            )

            if strategy_job_result.signals:
                with SorUnitOfWork(session) as uow:
                    next_state = execution_context.strategy_runtime_state_service.apply_event(
                        uow=uow,
                        strategy_id=manifest.strategy_id,
                        event=StrategyEvent.SIGNAL_GENERATED,
                        now_utc=now_utc,
                    )
                    persisted_state = (
                        execution_context.strategy_runtime_state_service.get_current_state(
                            uow=uow,
                            strategy_id=manifest.strategy_id,
                        )
                    )
                    print(
                        f"[EVAL] strategy_id={manifest.strategy_id} "
                        f"next_state={next_state} persisted_state={persisted_state}"
                    )
            if strategy_job_result.target_bar_timestamp is None:
                raise ValueError("target_bar_timestamp is required for order intent generation")

            signal_symbols = sorted({signal.symbol for signal in strategy_job_result.signals})

            generated_intents = (
                execution_context.portfolio_construction_service.generate_order_intents(
                    signals=strategy_job_result.signals,
                    positions={},
                    prices={symbol: 500.0 for symbol in signal_symbols},
                    run_id=manifest.run_id,
                    strategy_id=manifest.strategy_id,
                    bar_timestamp=strategy_job_result.target_bar_timestamp,
                    now=now_utc,
                )
            )

        duration = perf_counter() - job_start
        record_job_completed(
            logger=logger,
            metrics=TRADING_EVALUATION_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(manifest.run_id),
            duration_seconds=duration,
        )
        return strategy_job_result, generated_intents

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=TRADING_EVALUATION_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(manifest.run_id),
            exc=exc,
            duration_seconds=duration,
            failure_class=(
                "transient" if isinstance(exc, TransientInfrastructureError) else "unknown"
            ),
        )
        raise
