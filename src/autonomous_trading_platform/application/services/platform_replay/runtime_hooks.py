"""Runtime (trading cycle) domain replay hook."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    TradingCycleReplayResult,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


def run_trading_cycle_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
    broker_client: object | None = None,
    backtest_dataset_version_id: str | None = None,
) -> TradingCycleReplayResult:
    """Run one trading cycle at timestamp T.

    When broker_client is provided (intraday replay), it is reused across ticks
    so cash/position state persists within the day. Otherwise a new client is
    built per call (daily cadence backwards-compatible behaviour).
    """
    base = dict(
        domain="runtime",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    # Read controls state to detect pre-existing blocks
    ctrl_state = RuntimeControlStateRepository(session).get_global_state()
    if ctrl_state is not None:
        if ctrl_state.kill_switch_enabled:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="kill_switch_enabled",
                warnings=["Trading cycle skipped — kill switch enabled"],
            )
        if not ctrl_state.trading_enabled:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="trading_disabled",
                warnings=["Trading cycle skipped — trading disabled"],
            )
        if ctrl_state.trading_paused:
            return TradingCycleReplayResult(
                **base,
                status="skipped",
                cycle_blocked=True,
                block_reason="trading_paused",
                warnings=["Trading cycle skipped — trading paused"],
            )

    if dry_run or replay_context.dry_run:
        return TradingCycleReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    if broker_client is None:
        broker_client = _build_simulated_broker_client(
            session=session,
            timestamp=timestamp,
            dataset_version_id=backtest_dataset_version_id,
        )
    else:
        # Advance the shared client to the current tick timestamp
        broker_client.advance_to(timestamp)  # type: ignore[union-attr,attr-defined]

    orders_before = broker_client.orders_submitted_count  # type: ignore[union-attr,attr-defined]
    fills_before = broker_client.fills_count  # type: ignore[union-attr,attr-defined]

    try:
        run_trading_cycle(
            now_utc=timestamp,
            broker_client=broker_client,
            dataset_version_id_override=backtest_dataset_version_id,
        )
    except Exception as exc:
        import traceback

        print(
            f"[TRADING_CYCLE_ERROR] {timestamp.isoformat()}: {exc}\n{traceback.format_exc()}",
            flush=True,
        )
        return TradingCycleReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    orders_this_tick = broker_client.orders_submitted_count - orders_before  # type: ignore[union-attr,attr-defined]
    fills_this_tick = broker_client.fills_count - fills_before  # type: ignore[union-attr,attr-defined]

    return TradingCycleReplayResult(
        **base,
        status="ok",
        orders_submitted=orders_this_tick,
        fills_received=fills_this_tick,
        summary={
            "timestamp": timestamp.isoformat(),
            "orders_submitted": orders_this_tick,
            "fills_received": fills_this_tick,
        },
    )


def _build_simulated_broker_client(
    *,
    session: Session,
    timestamp: datetime,
    dataset_version_id: str | None = None,
    starting_cash: Decimal = Decimal("1000000"),
) -> object:
    """Build a SimulatedBrokerClient wired to the research simulation fill engine."""

    from autonomous_trading_platform.execution.clients.simulated_broker_client import (
        SimulatedBrokerClient,
    )
    from autonomous_trading_platform.research.simulation.models.fill_model import (
        SimulatedFillModelConfig,
    )
    from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
        VolumeShareSlippageModel,
    )
    from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
        SimulatedExecutionService,
    )
    from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
        SimulationCostModelConfig,
        SimulationCostModelService,
    )

    cost_model_service = SimulationCostModelService(
        config=SimulationCostModelConfig(
            commission_per_share=Decimal("0.0000"),
            min_commission=Decimal("0.00"),
        ),
        slippage_model=VolumeShareSlippageModel(),
    )
    execution_service = SimulatedExecutionService(
        simulation_cost_model_service=cost_model_service,
        fill_model_config=SimulatedFillModelConfig(
            max_volume_participation_rate=0.05,
        ),
    )

    return SimulatedBrokerClient(
        session=session,
        timestamp=timestamp,
        simulated_execution_service=execution_service,
        dataset_version_id=dataset_version_id,
        starting_cash=starting_cash,
    )
