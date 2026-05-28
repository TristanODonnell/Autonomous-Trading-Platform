# autonomous_trading_platform/scheduler/jobs/run_risk_snapshot_job.py

from __future__ import annotations

from decimal import Decimal
from time import perf_counter

from autonomous_trading_platform.common.errors import TransientInfrastructureError
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    risk_snapshot_job_duration,
    risk_snapshot_job_failures,
    risk_snapshot_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.risk.correlation_service import average_pairwise_correlation
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

RISK_SNAPSHOT_JOB_METRICS = JobMetricSet(
    runs=risk_snapshot_job_runs,
    failures=risk_snapshot_job_failures,
    duration=risk_snapshot_job_duration,
)


def run_risk_snapshot_job(
    *,
    now_utc,
    trading_cycle_dependencies,
    run_id,
) -> None:
    component = "scheduler.jobs.risk_snapshot_job"
    job = "risk_snapshot_job"
    job_start = perf_counter()
    session = trading_cycle_dependencies.session
    settings = trading_cycle_dependencies.settings
    risk_snapshot_service = RiskSnapshotService()

    risk_ctx = getattr(trading_cycle_dependencies, "risk_context", None)

    record_job_started(
        logger=logger,
        metrics=RISK_SNAPSHOT_JOB_METRICS,
        job=job,
        component=component,
        run_id=str(run_id),
    )

    try:
        with start_span("risk_snapshot_job.run", timespan=SpanTimespan.JOB) as job_span:
            job_span.set_attribute("ratp.run_id", run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", now_utc.isoformat())

            with SorUnitOfWork(session) as uow:
                latest_position_snapshot = (
                    uow.position_snapshots.get_latest()
                    if hasattr(uow.position_snapshots, "get_latest")
                    else None
                )
                latest_cash_snapshot = (
                    uow.cash_snapshots.get_latest()
                    if hasattr(uow.cash_snapshots, "get_latest")
                    else None
                )

                risk_snapshot = risk_snapshot_service.compute_snapshot(
                    run_id=run_id,
                    timestamp=now_utc,
                    position_snapshot=latest_position_snapshot,
                    cash_snapshot=latest_cash_snapshot,
                    limits_config=RiskLimitConfig(
                        max_gross_exposure=Decimal(str(settings.max_gross_exposure)),
                        max_net_exposure=Decimal(str(settings.max_net_exposure)),
                        max_leverage=Decimal(str(settings.max_leverage)),
                        max_portfolio_symbol_exposure_usd=_optional_decimal_setting(
                            settings,
                            "max_portfolio_symbol_exposure_usd",
                        ),
                        max_portfolio_symbol_pct=_optional_decimal_setting(
                            settings,
                            "max_portfolio_symbol_pct",
                        ),
                    ),
                    drawdown_pct=None,
                )
                uow.risk_snapshots.upsert(risk_snapshot)

                if risk_ctx is not None:
                    _run_story29_risk_checks(
                        risk_ctx=risk_ctx,
                        position_snapshot=latest_position_snapshot,
                        cash_snapshot=latest_cash_snapshot,
                        settings=settings,
                        run_id=run_id,
                    )

        duration = perf_counter() - job_start
        record_job_completed(
            logger=logger,
            metrics=RISK_SNAPSHOT_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(run_id),
            duration_seconds=duration,
        )

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=RISK_SNAPSHOT_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=duration,
            failure_class=(
                "transient" if isinstance(exc, TransientInfrastructureError) else "unknown"
            ),
        )
        raise


def _run_story29_risk_checks(
    *,
    risk_ctx,
    position_snapshot,
    cash_snapshot,
    settings,
    run_id,
) -> None:
    """
    Run the full STORY-29 risk stack using data already fetched by the job.

    Derives positions and prices directly from the PositionSnapshot that
    was already loaded above — no additional DB calls.

    Failure is caught and logged but never re-raised: portfolio monitoring
    must never block the cycle or freeze trading.
    """
    try:
        positions: dict[str, int] = {}
        prices: dict[str, float] = {}

        if position_snapshot is not None:
            for pos in position_snapshot.positions:
                qty = int(pos.quantity)
                if qty == 0:
                    continue
                positions[pos.symbol] = qty
                if pos.market_price is not None:
                    prices[pos.symbol] = float(pos.market_price)

        total_capital: float = (
            float(cash_snapshot.equity)
            if cash_snapshot is not None and cash_snapshot.equity is not None
            else 0.0
        )

        snapshot = risk_ctx.risk_engine.compute_exposures(
            positions=positions,
            prices=prices,
            strategy_map=_resolve_strategy_map(settings),
            sector_map=_resolve_sector_map(settings),
        )

        equity_curve = _resolve_equity_curve(settings)
        realized_vol: float | None = None
        if equity_curve:
            realized_vol = risk_ctx.portfolio_vol_targeting_service.compute_realized_vol(
                equity_curve
            )

        strategy_returns = _resolve_strategy_returns(settings)
        avg_corr: float | None = None
        if strategy_returns and len(strategy_returns) >= 2:
            avg_corr = average_pairwise_correlation(strategy_returns)

        alerts = risk_ctx.risk_alert_service.evaluate(
            snapshot=snapshot,
            total_capital=total_capital,
            equity_curve=equity_curve,
            realized_vol=realized_vol,
            avg_correlation=avg_corr,
        )

        rebalance_report = risk_ctx.rebalancing_service.compute_rebalance(
            snapshot=snapshot,
            total_capital_usd=total_capital,
        )

        if rebalance_report.needs_rebalance:
            logger.info(
                "risk_snapshot.rebalance_needed",
                extra={
                    "run_id": str(run_id),
                    "strategies_drifted": len(rebalance_report.deltas),
                    "max_drift": float(rebalance_report.max_drift),
                    "actions": [
                        {
                            "strategy_id": d.strategy_id,
                            "action": d.action,
                            "current_weight": float(d.current_weight),
                            "target_weight": float(d.target_weight),
                            "drift": float(d.drift),
                            "suggested_capital_usd": float(d.suggested_capital_usd),
                        }
                        for d in rebalance_report.deltas
                    ],
                },
            )

        logger.info(
            "risk_snapshot.story29.completed",
            extra={
                "run_id": str(run_id),
                "position_count": len(positions),
                "total_gross_exposure_usd": float(snapshot.total_gross_exposure_usd),
                "total_net_exposure_usd": float(snapshot.total_net_exposure_usd),
                "max_strategy_weight": float(snapshot.max_strategy_weight),
                "strategy_hhi": float(snapshot.strategy_hhi),
                "realized_annual_vol": round(realized_vol, 4) if realized_vol else None,
                "avg_pairwise_correlation": round(avg_corr, 4) if avg_corr else None,
                "alerts_fired": len(alerts),
                "rebalance_needed": rebalance_report.needs_rebalance,
            },
        )

    except Exception as exc:
        # Non-fatal — monitoring must never block or freeze trading
        logger.warning(
            "risk_snapshot.story29.failed",
            extra={"run_id": str(run_id), "error": str(exc)},
            exc_info=True,
        )


def _optional_decimal_setting(settings, name: str) -> Decimal | None:
    value = getattr(settings, name, None)
    if value is None:
        return None
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Resolvers — wire as the platform grows, safe no-ops until then
# ---------------------------------------------------------------------------


def _resolve_strategy_map(settings) -> dict[str, str] | None:
    """
    {symbol: strategy_id} — enables per-strategy exposure breakdown (TASK-194).
    Wire to: universe_service.get_symbol_strategy_map()
    """
    return None


def _resolve_sector_map(settings) -> dict[str, str] | None:
    """
    {symbol: sector_name} — enables per-sector exposure + alerts (TASK-194/199).
    Wire to: reference_data_service.get_symbol_sector_map()
    """
    return None


def _resolve_equity_curve(settings) -> list[float] | None:
    """
    Ordered daily portfolio equity values, most-recent last.
    Enables drawdown and portfolio vol checks (TASK-197/199).
    Wire to: performance_repository.get_daily_equity_curve()
    """
    return None


def _resolve_strategy_returns(settings) -> dict[str, list[float]] | None:
    """
    {strategy_id: list[daily_returns]} for pairwise correlation (TASK-195/199).
    Wire to: performance_repository.get_strategy_daily_returns()
    """
    return None
