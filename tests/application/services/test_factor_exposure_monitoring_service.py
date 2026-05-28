from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.factor_exposure_monitoring_service import (
    FactorExposureMonitoringConfig,
    FactorExposureMonitoringService,
    FactorExposureThresholds,
    _annualized_volatility,
    _log_returns,
    _trailing_cumulative_return,
)
from autonomous_trading_platform.contracts.runtime.factor_exposure import (
    FactorExposureInputPosition,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.repositories.core.factor_exposure_snapshot_repository import (
    FactorExposureSnapshotRepository,
)

_NOW = datetime(2026, 5, 27, 16, 0, 0, tzinfo=UTC)


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


def _prices_from_returns(start: float, returns: list[float]) -> list[float]:
    prices = [start]
    for ret in returns:
        prices.append(prices[-1] * math.exp(ret))
    return prices


def test_factor_math_helpers() -> None:
    returns = _log_returns([100.0, 105.0, 110.0])
    assert math.isclose(returns[0], math.log(105.0 / 100.0), rel_tol=1e-9)
    assert _trailing_cumulative_return([math.log(1.1)]) == 0.1
    assert _annualized_volatility([0.01, -0.01, 0.02], 252) is not None


def test_beta_momentum_and_volatility_computation(db_session: Session) -> None:
    benchmark_returns = [0.01 if i % 2 == 0 else -0.005 for i in range(40)]
    symbol_returns = [2.0 * r for r in benchmark_returns]
    _seed_market_bars(db_session, "SPY", _prices_from_returns(100.0, benchmark_returns))
    _seed_market_bars(db_session, "BETA2", _prices_from_returns(50.0, symbol_returns))

    svc = FactorExposureMonitoringService(
        session=db_session,
        config=FactorExposureMonitoringConfig(
            factor_exposure_windows=[20],
            minimum_observations_required=10,
            thresholds=FactorExposureThresholds(max_abs_beta_exposure=3.0),
        ),
    )
    result = svc.run(
        as_of=_NOW,
        portfolio_id="paper",
        positions=[
            FactorExposureInputPosition(
                symbol="BETA2",
                weight=1.0,
                strategy_id="trend",
                sector="Technology",
                market_cap=1_000_000_000,
            )
        ],
        run_id="factor-run-1",
    )

    exposure = result.portfolio_exposures[20]
    assert exposure["market_beta"] is not None
    assert 1.8 < exposure["market_beta"] < 2.2
    assert exposure["momentum"] is not None
    assert exposure["volatility"] is not None


def test_strategy_and_portfolio_aggregation(db_session: Session) -> None:
    returns = [0.01, 0.0, -0.005, 0.02] * 10
    _seed_market_bars(db_session, "SPY", _prices_from_returns(100.0, returns))
    _seed_market_bars(db_session, "AAA", _prices_from_returns(50.0, returns))
    _seed_market_bars(db_session, "BBB", _prices_from_returns(40.0, [r * 0.5 for r in returns]))

    svc = FactorExposureMonitoringService(
        session=db_session,
        config=FactorExposureMonitoringConfig(
            factor_exposure_windows=[20], minimum_observations_required=10
        ),
    )
    svc.run(
        as_of=_NOW,
        portfolio_id="paper",
        positions=[
            FactorExposureInputPosition(
                symbol="AAA", weight=0.75, strategy_id="s1", sector="Technology"
            ),
            FactorExposureInputPosition(
                symbol="BBB", weight=0.25, strategy_id="s2", sector="Financials"
            ),
        ],
    )

    repo = FactorExposureSnapshotRepository(db_session)
    snap = repo.get_latest_snapshot(portfolio_id="paper", lookback_window=20)
    assert snap is not None
    assert len(snap.strategy_exposures) == 2
    assert snap.sector_exposures["Technology"] == 0.75
    assert snap.portfolio_exposures["market_beta"] is not None


def test_hidden_concentration_detection_and_persistence(db_session: Session) -> None:
    benchmark_returns = [0.015 if i % 2 == 0 else -0.01 for i in range(40)]
    _seed_market_bars(db_session, "SPY", _prices_from_returns(100.0, benchmark_returns))
    _seed_market_bars(
        db_session, "HIGHB", _prices_from_returns(20.0, [3 * r for r in benchmark_returns])
    )
    _seed_market_bars(
        db_session, "HIGHM", _prices_from_returns(30.0, [abs(r) for r in benchmark_returns])
    )

    svc = FactorExposureMonitoringService(
        session=db_session,
        config=FactorExposureMonitoringConfig(
            factor_exposure_windows=[20],
            minimum_observations_required=10,
            thresholds=FactorExposureThresholds(
                max_abs_beta_exposure=1.0,
                max_abs_momentum_exposure=0.05,
                max_sector_concentration=0.60,
            ),
        ),
    )
    result = svc.run(
        as_of=_NOW,
        portfolio_id="paper",
        positions=[
            FactorExposureInputPosition(
                symbol="HIGHB", weight=0.5, strategy_id="s1", sector="Technology"
            ),
            FactorExposureInputPosition(
                symbol="HIGHM", weight=0.5, strategy_id="s2", sector="Technology"
            ),
        ],
        run_id="factor-run-conc",
    )

    assert result.concentration_alerts
    assert any(alert["factor_name"] == "sector" for alert in result.concentration_alerts)
    repo = FactorExposureSnapshotRepository(db_session)
    persisted = repo.get_snapshots_for_run("factor-run-conc")
    assert len(persisted) == 1
    assert persisted[0].factor_computation_version == "factor_exposure_v1"


def test_insufficient_history_emits_diagnostics(db_session: Session) -> None:
    _seed_market_bars(db_session, "SPY", [100.0, 101.0, 102.0])
    _seed_market_bars(db_session, "NEW", [50.0, 51.0, 52.0])

    svc = FactorExposureMonitoringService(
        session=db_session,
        config=FactorExposureMonitoringConfig(
            factor_exposure_windows=[20], minimum_observations_required=20
        ),
    )
    result = svc.run(
        as_of=_NOW,
        positions=[FactorExposureInputPosition(symbol="NEW", weight=1.0)],
    )

    assert result.snapshots_persisted == 1
    assert result.portfolio_exposures[20]["market_beta"] is None
    assert any("Insufficient observations" in warning for warning in result.warnings)


def test_deterministic_historical_replay(db_session: Session) -> None:
    returns = [0.01, -0.003, 0.004, 0.002] * 10
    _seed_market_bars(db_session, "SPY", _prices_from_returns(100.0, returns))
    _seed_market_bars(db_session, "AAA", _prices_from_returns(50.0, returns))
    config = FactorExposureMonitoringConfig(
        factor_exposure_windows=[20], minimum_observations_required=10
    )
    positions = [FactorExposureInputPosition(symbol="AAA", weight=1.0, strategy_id="s1")]

    first = FactorExposureMonitoringService(session=db_session, config=config).run(
        as_of=_NOW, positions=positions, run_id="replay-1"
    )
    second = FactorExposureMonitoringService(session=db_session, config=config).run(
        as_of=_NOW, positions=positions, run_id="replay-2"
    )

    assert first.portfolio_exposures == second.portfolio_exposures
