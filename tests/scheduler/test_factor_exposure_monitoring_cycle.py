from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

import autonomous_trading_platform.scheduler.cycles.run_factor_exposure_monitoring_cycle as cycle_module
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.contracts.runtime.factor_exposure import (
    FactorExposureInputPosition,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns

_NOW = datetime(2026, 5, 27, 16, 0, tzinfo=UTC)


def test_factor_exposure_monitoring_cycle_persists_snapshot(
    db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cycle_module, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    _seed_market_bars(db_session, "SPY", [100.0 + i for i in range(35)])
    _seed_market_bars(db_session, "AAA", [50.0 + i for i in range(35)])

    result = cycle_module.run_factor_exposure_monitoring_cycle(
        now_utc=_NOW,
        positions=[
            FactorExposureInputPosition(
                symbol="AAA",
                weight=1.0,
                strategy_id="s1",
                sector="Technology",
            )
        ],
        factor_exposure_windows=[20],
    )

    assert result["snapshots_persisted"] == 1
    job = (
        db_session.query(RuntimeJobRuns)
        .filter_by(job_name="factor_exposure_monitoring_cycle")
        .one()
    )
    assert job.status == "completed"
    manifest = db_session.query(RunManifestRow).filter_by(run_id=UUID(job.correlation_id)).one()
    assert manifest.run_type == RunType.GOVERNANCE
    assert manifest.artifact_manifest["governance_action"] == "factor_exposure_monitoring"


def _seed_market_bars(session: Session, symbol: str, prices: list[float]) -> None:
    base_ts = _NOW - timedelta(days=len(prices))
    for i, price in enumerate(prices):
        ts = base_ts + timedelta(days=i)
        session.add(
            MarketBar(
                bar_id=f"{symbol}_{i}_{uuid4().hex[:8]}",
                timestamp=ts,
                end_timestamp=ts + timedelta(hours=6, minutes=30),
                interval="1d",
                symbol=symbol,
                open=Decimal(str(price)),
                high=Decimal(str(price * 1.01)),
                low=Decimal(str(price * 0.99)),
                close=Decimal(str(price)),
                volume=100000,
                vwap=None,
                trade_count=None,
                price_basis="adjusted",
                adjustment_factor=1.0,
                source="test",
                ingested_at=_NOW,
                quality_flags=[],
                session="regular",
            )
        )
    session.flush()
