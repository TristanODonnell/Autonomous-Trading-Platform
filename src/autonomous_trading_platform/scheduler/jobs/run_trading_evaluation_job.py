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
from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
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


def _fetch_positions(broker_client) -> dict[str, Position]:
    """
    Fetch current open positions from the broker and convert to
    the internal Position contract.

    Returns an empty dict if the broker returns no positions.
    """
    raw_positions = broker_client.get_positions()
    positions: dict[str, Position] = {}

    for raw in raw_positions:
        symbol = raw["symbol"]
        quantity = Decimal(raw["qty"])
        avg_cost = Decimal(raw["avg_entry_price"])
        market_price = Decimal(raw["current_price"])
        market_value = Decimal(raw["market_value"])
        unrealized_pnl = Decimal(raw["unrealized_pl"])

        positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
            market_price=market_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
        )

    return positions


def _fetch_prices(broker_client, symbols: list[str]) -> dict[str, float]:
    """
    Fetch latest trade prices for the given symbols from the broker.

    Falls back to None for any symbol the broker doesn't return a price
    for — the PositionSizer will skip those symbols with a warning.
    """
    if not symbols:
        return {}

    raw_trades = broker_client.get_latest_trades(symbols)
    prices: dict[str, float] = {}

    for symbol in symbols:
        trade = raw_trades.get(symbol)
        if trade is not None:
            prices[symbol] = float(trade["p"])
        else:
            logger.warning(
                "evaluation_job.missing_price",
                extra={"symbol": symbol},
            )

    return prices


def _fetch_equity(broker_client) -> float | None:
    """
    Fetch current account equity from the broker to keep PortfolioEngine
    total_capital in sync with realised P&L.

    Returns None if the field is missing — caller will skip the update.
    """
    account = broker_client.get_account()
    equity_str = account.get("equity")
    if equity_str is None:
        return None
    return float(equity_str)


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

            # --- Sync total_capital with real broker equity ---
            # Keeps the PositionSizer working off current equity after
            # each cycle's P&L settlement rather than a stale config value.
            equity = _fetch_equity(broker_client)
            if equity is not None and equity > 0:
                portfolio_engine.update_total_capital(equity)
                logger.info(
                    "evaluation_job.capital_synced",
                    extra={"equity": equity},
                )

            # --- Strategy evaluation ---
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

            if strategy_job_result.target_bar_timestamp is None:
                raise ValueError("target_bar_timestamp is required for order intent generation")

            signal_symbols = sorted({signal.symbol for signal in strategy_job_result.signals})

            # --- Fetch real positions and prices ---
            positions = _fetch_positions(broker_client)
            prices = _fetch_prices(broker_client, signal_symbols)

            job_span.set_attribute("ratp.position_count", len(positions))
            job_span.set_attribute("ratp.signal_count", len(strategy_job_result.signals))
            job_span.set_attribute("ratp.price_count", len(prices))

            # --- Resolve approval_status for this strategy ---
            # In v1 the manifest carries the governance state; this is
            # the bridge between governance and the portfolio/sizing layer.
            approval_status = GovernanceState(manifest.governance_state)

            # --- Generate sized order intents ---
            generated_intents = (
                execution_context.portfolio_construction_service.generate_order_intents(
                    signals=strategy_job_result.signals,
                    positions=positions,
                    prices=prices,
                    run_id=manifest.run_id,
                    strategy_id=manifest.strategy_id,
                    approval_status=approval_status,
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
