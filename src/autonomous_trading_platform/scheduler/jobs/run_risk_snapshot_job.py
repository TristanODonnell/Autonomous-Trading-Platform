from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def run_risk_snapshot_job(
    *,
    now_utc,
    trading_cycle_dependencies,
    run_id,
) -> None:
    session = trading_cycle_dependencies.session
    settings = trading_cycle_dependencies.settings
    risk_snapshot_service = RiskSnapshotService()

    with SorUnitOfWork(session) as uow:
        latest_position_snapshot = (
            uow.position_snapshots.get_latest()
            if hasattr(uow.position_snapshots, "get_latest")
            else None
        )
        latest_cash_snapshot = (
            uow.cash_snapshots.get_latest() if hasattr(uow.cash_snapshots, "get_latest") else None
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
            ),
            drawdown_pct=None,
        )

        uow.risk_snapshots.upsert(risk_snapshot)
