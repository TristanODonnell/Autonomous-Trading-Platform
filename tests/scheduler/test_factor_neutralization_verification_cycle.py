from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

import autonomous_trading_platform.scheduler.cycles.run_factor_neutralization_verification_cycle as cycle_module
from autonomous_trading_platform.application.services.factor_exposure_monitoring_service import (
    FactorExposureMonitoringConfig,
    FactorExposureMonitoringService,
)
from autonomous_trading_platform.contracts.runtime.factor_exposure import (
    FactorExposureInputPosition,
)
from autonomous_trading_platform.contracts.runtime.factor_neutralization import (
    FactorNeutralizationMode,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar

_NOW = datetime(2026, 5, 27, 16, 0, tzinfo=UTC)


def test_factor_neutralization_verification_cycle_uses_latest_factor_snapshot(
    db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cycle_module, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    _seed_market_bars(db_session, "SPY", [100.0 + i for i in range(35)])
    _seed_market_bars(db_session, "AAA", [50.0 + i for i in range(35)])
    FactorExposureMonitoringService(
        session=db_session,
        config=FactorExposureMonitoringConfig(
            factor_exposure_windows=[20], minimum_observations_required=10
        ),
    ).run(
        as_of=_NOW,
        portfolio_id="paper",
        positions=[
            FactorExposureInputPosition(
                symbol="AAA", weight=1.0, strategy_id="s1", sector="Technology"
            )
        ],
        run_id="factor-source",
    )

    result = cycle_module.run_factor_neutralization_verification_cycle(
        now_utc=_NOW,
        portfolio_id="paper",
        mode=FactorNeutralizationMode.OBSERVE_ONLY,
        enabled=True,
    )

    assert result["run_id"] is not None
    assert result["portfolio_id"] == "paper"
    assert result["factor_snapshot_id"] is not None


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
