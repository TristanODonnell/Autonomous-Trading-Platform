# autonomous_trading_platform/scheduler/jobs/run_trading_evaluation_job.py

from datetime import datetime
from decimal import Decimal
from time import perf_counter

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import StrategyEvent
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.trading.signal_aggregate import SignalNettingPolicy
from autonomous_trading_platform.execution.services.portfolio_signal_aggregator import (
    PortfolioSignalAggregator,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
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

# Number of recent closes to fetch per symbol for vol computation.
# 20 bars ≈ ~25 minutes of 5-min data — enough for intraday vol estimate.
_VOL_LOOKBACK_BARS = 20


def _fetch_positions(broker_client) -> dict[str, Position]:
    raw_positions = broker_client.get_positions()
    positions: dict[str, Position] = {}

    for raw in raw_positions:
        symbol = raw["symbol"]
        positions[symbol] = Position(
            symbol=symbol,
            quantity=Decimal(raw["qty"]),
            avg_cost=Decimal(raw["avg_entry_price"]),
            market_price=Decimal(raw["current_price"]),
            market_value=Decimal(raw["market_value"]),
            unrealized_pnl=Decimal(raw["unrealized_pl"]),
        )

    return positions


def _fetch_prices(broker_client, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}

    raw_trades = broker_client.get_latest_trades(symbols)
    prices: dict[str, float] = {}

    for symbol in symbols:
        trade = raw_trades.get(symbol)
        if trade is not None:
            prices[symbol] = float(trade["p"])
        else:
            logger.warning("evaluation_job.missing_price", extra={"symbol": symbol})

    return prices


def _fetch_equity(broker_client) -> float | None:
    account = broker_client.get_account()
    equity_str = account.get("equity")
    if equity_str is None:
        return None
    return float(equity_str)


def _fetch_recent_closes(
    strategy_context,
    symbols: list[str],
    bar_timestamp: datetime,
    lookback_bars: int,
) -> dict[str, list[float]]:
    """
    Fetch recent close prices per symbol from Parquet bar store.
    Used by VolatilityScalingService to compute realized vol.

    Returns an empty list for any symbol where bars are unavailable —
    the scaling service will skip vol scaling for that symbol.
    """
    from datetime import timedelta

    recent_closes: dict[str, list[float]] = {}

    for symbol in symbols:
        try:
            bars = strategy_context.strategy_evaluation_service.context_builder.market_bar_reader.read(
                dataset=strategy_context.strategy_evaluation_service.context_builder.bars_dataset,
                dataset_version=strategy_context.strategy_evaluation_service.context_builder.dataset_version,
                symbol=symbol,
                start_date=(bar_timestamp - timedelta(days=5)).date(),
                end_date=bar_timestamp.date(),
            )

            if bars.num_rows == 0:
                recent_closes[symbol] = []
                continue

            rows = sorted(
                [r for r in bars.to_pylist() if r["timestamp"] < bar_timestamp],
                key=lambda r: r["timestamp"],
            )
            recent_closes[symbol] = [float(r["close"]) for r in rows[-lookback_bars:]]

        except Exception as exc:
            logger.warning(
                "evaluation_job.recent_closes_fetch_failed",
                extra={"symbol": symbol, "reason": str(exc)},
            )
            recent_closes[symbol] = []

    return recent_closes


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
    portfolio_engine = trading_cycle_dependencies.portfolio_engine
    broker_client = execution_context.broker_client

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

            # Sync total_capital with real broker equity each cycle
            equity = _fetch_equity(broker_client)
            if equity is not None and equity > 0:
                portfolio_engine.update_total_capital(equity)
                logger.info("evaluation_job.capital_synced", extra={"equity": equity})
                if hasattr(trading_cycle_dependencies.audit_logger, "record_event"):
                    trading_cycle_dependencies.audit_logger.record_event(
                        run_id=str(manifest.run_id),
                        event_type="BROKER_SYNC_COMPLETED",
                        component=component,
                        message="Broker account equity synchronized",
                        metadata={
                            "equity": str(equity),
                            "severity": "info",
                        },
                    )

            # Strategy evaluation
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
                    logger.info(
                        "evaluation_job.strategy_state_transition",
                        extra={
                            "strategy_id": manifest.strategy_id,
                            "next_state": str(next_state),
                            "persisted_state": str(persisted_state),
                        },
                    )

            if not strategy_job_result.evaluated:
                logger.info(
                    "evaluation_job.bars_not_ready",
                    extra={"reason": strategy_job_result.reason},
                )
                record_job_completed(
                    logger=logger,
                    metrics=TRADING_EVALUATION_JOB_METRICS,
                    job=job,
                    component=component,
                    run_id=str(manifest.run_id),
                    duration_seconds=perf_counter() - job_start,
                )
                return strategy_job_result, iter([])

            signal_symbols = sorted({signal.symbol for signal in strategy_job_result.signals})
            bar_timestamp: datetime = strategy_job_result.target_bar_timestamp  # type: ignore[assignment]

            # Fetch real positions and prices — include position symbols so that
            # build_order_intent can price exit deltas for holdings with no new signal.
            positions = _fetch_positions(broker_client)
            position_symbols = sorted(positions.keys())
            all_price_symbols = sorted(set(signal_symbols) | set(position_symbols))
            prices = _fetch_prices(broker_client, all_price_symbols)

            recent_closes = _fetch_recent_closes(
                strategy_context=strategy_context,
                symbols=signal_symbols,
                bar_timestamp=bar_timestamp,
                lookback_bars=_VOL_LOOKBACK_BARS,
            )

            job_span.set_attribute("ratp.position_count", len(positions))
            job_span.set_attribute("ratp.signal_count", len(strategy_job_result.signals))
            job_span.set_attribute("ratp.price_count", len(prices))

            approval_status = GovernanceState(manifest.governance_state)

            # Portfolio-level signal aggregation: reconcile multi-strategy intent before
            # order generation.  With a single active strategy the aggregator is a
            # transparent pass-through; it becomes load-bearing as additional strategies
            # are introduced into the same trading cycle.
            aggregator = PortfolioSignalAggregator(policy=SignalNettingPolicy.CONSERVATIVE)
            aggregation_result = aggregator.aggregate(
                signals_by_strategy={manifest.strategy_id: strategy_job_result.signals},
                run_id=manifest.run_id,
                bar_timestamp=bar_timestamp,
                prices=prices,
            )

            job_span.set_attribute(
                "ratp.aggregation_conflicts", aggregation_result.total_conflicts_detected
            )
            job_span.set_attribute(
                "ratp.aggregation_suppressed", aggregation_result.total_suppressed_symbols
            )
            job_span.set_attribute(
                "ratp.aggregation_reconciled_count", len(aggregation_result.reconciled_signals)
            )

            if aggregation_result.total_conflicts_detected > 0:
                logger.warning(
                    "evaluation_job.signal_conflicts_detected",
                    extra={
                        "run_id": str(manifest.run_id),
                        "conflict_count": aggregation_result.total_conflicts_detected,
                        "suppressed_count": aggregation_result.total_suppressed_symbols,
                        "policy": aggregation_result.policy.value,
                    },
                )

            generated_intents = (
                execution_context.portfolio_construction_service.generate_order_intents(
                    signals=aggregation_result.reconciled_signals,
                    positions=positions,
                    prices=prices,
                    run_id=manifest.run_id,
                    strategy_id=manifest.strategy_id,
                    approval_status=approval_status,
                    bar_timestamp=bar_timestamp,
                    now=now_utc,
                    recent_closes=recent_closes,
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
