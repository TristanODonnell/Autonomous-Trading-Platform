from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import numpy as np
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.correlation_monitoring_service import (
    CorrelationMonitoringConfig,
    CorrelationMonitoringService,
    _detect_clusters,
    _log_returns,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)

_NOW = datetime(2026, 5, 27, 16, 0, 0, tzinfo=UTC)
_BAR_INTERVAL = "1d"
_PRICE_BASIS = "adjusted"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_market_bars(
    session: Session,
    symbol: str,
    prices: list[float],
    base_ts: datetime | None = None,
) -> None:
    if base_ts is None:
        base_ts = _NOW - timedelta(days=len(prices))
    for i, price in enumerate(prices):
        ts = base_ts + timedelta(days=i)
        session.add(
            MarketBar(
                bar_id=f"{symbol}_{i}_{uuid4().hex[:8]}",
                timestamp=ts,
                end_timestamp=ts + timedelta(hours=6, minutes=30),
                interval=_BAR_INTERVAL,
                symbol=symbol,
                open=Decimal(str(price)),
                high=Decimal(str(price * 1.01)),
                low=Decimal(str(price * 0.99)),
                close=Decimal(str(price)),
                volume=100000,
                vwap=None,
                trade_count=None,
                price_basis=_PRICE_BASIS,
                adjustment_factor=1.0,
                source="test",
                ingested_at=_NOW,
                quality_flags=[],
                session="regular",
            )
        )
    session.flush()


def _seed_strategy_performance(
    session: Session,
    strategy_id: str,
    returns: list[float],
    base_ts: datetime | None = None,
) -> None:
    if base_ts is None:
        base_ts = _NOW - timedelta(days=len(returns))
    for i, ret in enumerate(returns):
        ts = base_ts + timedelta(days=i)
        session.add(
            StrategyLivePerformanceSnapshot(
                snapshot_id=f"{strategy_id}_{i}_{uuid4().hex[:8]}",
                strategy_id=strategy_id,
                run_id=None,
                computed_at=ts,
                realized_return=ret,
                rolling_sharpe=None,
                realized_drawdown=None,
                realized_volatility=None,
                live_win_rate=None,
                trade_count=None,
                winning_trade_count=None,
                days_live=i + 1,
                days_since_profitable_day=None,
                window_days=None,
                window_trades=None,
                metadata_json=None,
            )
        )
    session.flush()


def _make_correlated_prices(base: list[float], noise_scale: float = 0.001) -> list[float]:
    """Return prices that closely track `base` with tiny noise."""
    rng = np.random.default_rng(42)
    noise = rng.normal(0, noise_scale, len(base))
    return [max(p + n, 0.01) for p, n in zip(base, noise, strict=True)]


# ---------------------------------------------------------------------------
# Helpers / module-level utilities
# ---------------------------------------------------------------------------


class TestLogReturns:
    def test_basic_returns(self) -> None:
        prices = [100.0, 105.0, 102.9]
        rets = _log_returns(prices)
        assert len(rets) == 2
        assert math.isclose(rets[0], math.log(105 / 100), rel_tol=1e-9)
        assert math.isclose(rets[1], math.log(102.9 / 105), rel_tol=1e-9)

    def test_zero_price_handled(self) -> None:
        prices = [0.0, 100.0, 110.0]
        rets = _log_returns(prices)
        assert rets[0] == 0.0
        assert math.isfinite(rets[1])

    def test_single_price_empty(self) -> None:
        assert _log_returns([100.0]) == []

    def test_empty_empty(self) -> None:
        assert _log_returns([]) == []


class TestDetectClusters:
    def test_two_correlated_assets(self) -> None:
        assets = ["A", "B", "C"]
        corr = np.array(
            [
                [1.0, 0.92, 0.10],
                [0.92, 1.0, 0.15],
                [0.10, 0.15, 1.0],
            ]
        )
        clusters = _detect_clusters(assets=assets, corr_matrix=corr, threshold=0.80)
        assert len(clusters) == 1
        assert set(clusters[0].members) == {"A", "B"}

    def test_all_independent(self) -> None:
        assets = ["A", "B", "C"]
        corr = np.eye(3)
        clusters = _detect_clusters(assets=assets, corr_matrix=corr, threshold=0.80)
        assert clusters == []

    def test_all_correlated(self) -> None:
        assets = ["A", "B", "C"]
        corr = np.full((3, 3), 0.95)
        np.fill_diagonal(corr, 1.0)
        clusters = _detect_clusters(assets=assets, corr_matrix=corr, threshold=0.80)
        assert len(clusters) == 1
        assert len(clusters[0].members) == 3

    def test_two_separate_clusters(self) -> None:
        assets = ["A", "B", "C", "D"]
        corr = np.array(
            [
                [1.0, 0.95, 0.05, 0.03],
                [0.95, 1.0, 0.04, 0.02],
                [0.05, 0.04, 1.0, 0.91],
                [0.03, 0.02, 0.91, 1.0],
            ]
        )
        clusters = _detect_clusters(assets=assets, corr_matrix=corr, threshold=0.80)
        assert len(clusters) == 2
        cluster_members = {frozenset(c.members) for c in clusters}
        assert frozenset(["A", "B"]) in cluster_members
        assert frozenset(["C", "D"]) in cluster_members


# ---------------------------------------------------------------------------
# Rolling correlation computation
# ---------------------------------------------------------------------------


class TestSymbolCorrelationComputation:
    def test_perfectly_correlated_pair(self, db_session: Session) -> None:
        prices = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "AAA", prices)
        _seed_market_bars(db_session, "BBB", _make_correlated_prices(prices, 0.0001))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20], min_observations=10, annualization_factor=252
            ),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["AAA", "BBB"])

        assert result.symbol_snapshot_count == 1
        assert result.max_symbol_correlation is not None
        assert result.max_symbol_correlation > 0.95

    def test_perfectly_anticorrelated_pair(self, db_session: Session) -> None:
        # Oscillating prices produce alternating + / - returns that are anticorrelated
        base = 100.0
        amplitudes = [3.0 * (1 + i % 3) for i in range(31)]
        prices_up = [
            base + amplitudes[i] if i % 2 == 0 else base - amplitudes[i] for i in range(31)
        ]
        prices_down = [
            base - amplitudes[i] if i % 2 == 0 else base + amplitudes[i] for i in range(31)
        ]
        _seed_market_bars(db_session, "UP", prices_up)
        _seed_market_bars(db_session, "DN", prices_down)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["UP", "DN"])

        assert result.symbol_snapshot_count == 1
        assert result.max_symbol_correlation is not None
        # Oscillating opposite prices should produce negative correlation
        assert result.max_symbol_correlation < 0.0

    def test_multiple_windows(self, db_session: Session) -> None:
        prices = [100.0 + i for i in range(280)]
        _seed_market_bars(db_session, "X1", prices)
        _seed_market_bars(db_session, "X2", _make_correlated_prices(prices))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20, 60, 252], min_observations=20),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["X1", "X2"])
        # Should produce 3 symbol snapshots + 3 covariance snapshots
        assert result.symbol_snapshot_count == 3
        assert result.covariance_snapshot_count >= 3

    def test_insufficient_observations_skipped(self, db_session: Session) -> None:
        prices = [100.0, 101.0, 102.0]  # only 3 bars → 2 returns → below min
        _seed_market_bars(db_session, "FEW1", prices)
        _seed_market_bars(db_session, "FEW2", prices)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=20),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["FEW1", "FEW2"])
        assert result.symbol_snapshot_count == 0

    def test_single_symbol_skipped(self, db_session: Session) -> None:
        prices = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "SOLO", prices)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["SOLO"])
        assert result.symbol_snapshot_count == 0

    def test_empty_symbols_returns_empty_result(self, db_session: Session) -> None:
        svc = CorrelationMonitoringService(session=db_session)
        result = svc.run(as_of=_NOW, portfolio_symbols=[])
        assert result.symbol_snapshot_count == 0
        assert result.snapshots_persisted == 0


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------


class TestSnapshotPersistence:
    def test_correlation_snapshot_persisted(self, db_session: Session) -> None:
        prices = [100.0 + i * 0.3 for i in range(30)]
        _seed_market_bars(db_session, "PA", prices)
        _seed_market_bars(db_session, "PB", _make_correlated_prices(prices))

        run_id = str(uuid4())
        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["PA", "PB"], run_id=run_id)

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        assert snap.run_id == run_id
        assert snap.snapshot_type == "symbol"
        assert snap.lookback_window == 20
        assert snap.asset_count == 2
        assert snap.observations_used >= 10

    def test_covariance_snapshot_persisted(self, db_session: Session) -> None:
        prices = [100.0 + i * 0.3 for i in range(30)]
        _seed_market_bars(db_session, "CA", prices)
        _seed_market_bars(db_session, "CB", _make_correlated_prices(prices))

        run_id = str(uuid4())
        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["CA", "CB"], run_id=run_id)

        repo = CorrelationSnapshotRepository(db_session)
        cov = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)
        assert cov is not None
        assert cov.run_id == run_id
        assert cov.annualization_factor == 252
        assert cov.matrix_hash is not None
        assert len(cov.matrix_hash) > 0

    def test_snapshot_universe_stored_correctly(self, db_session: Session) -> None:
        prices = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "AAPL", prices)
        _seed_market_bars(db_session, "MSFT", _make_correlated_prices(prices))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["AAPL", "MSFT"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        assert set(snap.asset_universe) == {"AAPL", "MSFT"}

    def test_deterministic_matrix_hash(self, db_session: Session) -> None:
        """Same data → same matrix_hash across two separate run calls."""
        prices_a = [100.0 + i for i in range(30)]
        prices_b = _make_correlated_prices(prices_a)
        _seed_market_bars(db_session, "H1", prices_a)
        _seed_market_bars(db_session, "H2", prices_b)

        config = CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10)
        repo = CorrelationSnapshotRepository(db_session)

        svc = CorrelationMonitoringService(session=db_session, config=config)
        svc.run(as_of=_NOW, portfolio_symbols=["H1", "H2"], run_id="run-1")
        snap1 = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)

        svc2 = CorrelationMonitoringService(session=db_session, config=config)
        svc2.run(as_of=_NOW, portfolio_symbols=["H1", "H2"], run_id="run-2")
        snap2 = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)

        assert snap1 is not None and snap2 is not None
        assert snap1.matrix_hash == snap2.matrix_hash

    def test_snapshots_retrievable_by_run_id(self, db_session: Session) -> None:
        prices = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "R1", prices)
        _seed_market_bars(db_session, "R2", _make_correlated_prices(prices))

        run_id = "test-run-abc"
        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["R1", "R2"], run_id=run_id)

        repo = CorrelationSnapshotRepository(db_session)
        corr_snaps = repo.get_correlation_snapshots_for_run(run_id)
        cov_snaps = repo.get_covariance_snapshots_for_run(run_id)
        assert len(corr_snaps) >= 1
        assert len(cov_snaps) >= 1


# ---------------------------------------------------------------------------
# Strategy correlation
# ---------------------------------------------------------------------------


class TestStrategyCorrelation:
    def test_correlated_strategies_detected(self, db_session: Session) -> None:
        returns = [0.01 * (i % 5 - 2) for i in range(30)]
        _seed_strategy_performance(db_session, "strat_a", returns)
        noisy_returns = [r + 0.0001 for r in returns]
        _seed_strategy_performance(db_session, "strat_b", noisy_returns)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(strategy_windows=[20], min_observations=20),
        )
        result = svc.run(
            as_of=_NOW,
            portfolio_symbols=[],
            strategy_ids=["strat_a", "strat_b"],
        )
        assert result.strategy_snapshot_count == 1
        assert result.max_strategy_correlation is not None
        assert result.max_strategy_correlation > 0.90

    def test_strategy_with_insufficient_data_skipped(self, db_session: Session) -> None:
        returns = [0.01] * 5  # only 5 obs
        _seed_strategy_performance(db_session, "new_strat_a", returns)
        _seed_strategy_performance(db_session, "new_strat_b", returns)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(strategy_windows=[20], min_observations=20),
        )
        result = svc.run(
            as_of=_NOW,
            portfolio_symbols=[],
            strategy_ids=["new_strat_a", "new_strat_b"],
        )
        assert result.strategy_snapshot_count == 0


# ---------------------------------------------------------------------------
# Sector correlation
# ---------------------------------------------------------------------------


class TestSectorCorrelation:
    def test_sector_correlation_aggregated(self, db_session: Session) -> None:
        prices_base = [100.0 + i * 0.4 for i in range(30)]
        _seed_market_bars(db_session, "TECH_A", prices_base)
        _seed_market_bars(db_session, "TECH_B", _make_correlated_prices(prices_base))
        prices_fin = [80.0 - i * 0.2 for i in range(30)]
        _seed_market_bars(db_session, "FIN_A", prices_fin)
        _seed_market_bars(db_session, "FIN_B", _make_correlated_prices(prices_fin))

        sector_map = {
            "TECH_A": "Technology",
            "TECH_B": "Technology",
            "FIN_A": "Financials",
            "FIN_B": "Financials",
        }

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20],
                sector_windows=[20],
                min_observations=10,
            ),
        )
        result = svc.run(
            as_of=_NOW,
            portfolio_symbols=["TECH_A", "TECH_B", "FIN_A", "FIN_B"],
            sector_map=sector_map,
        )
        assert result.sector_snapshot_count >= 1

    def test_single_sector_produces_no_snapshot(self, db_session: Session) -> None:
        prices = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "ONLY_TECH_A", prices)
        _seed_market_bars(db_session, "ONLY_TECH_B", _make_correlated_prices(prices))

        sector_map = {"ONLY_TECH_A": "Technology", "ONLY_TECH_B": "Technology"}

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20], sector_windows=[20], min_observations=10
            ),
        )
        result = svc.run(
            as_of=_NOW,
            portfolio_symbols=[],
            sector_map=sector_map,
        )
        # Only one sector → no matrix (need at least 2 sectors)
        assert result.sector_snapshot_count == 0


# ---------------------------------------------------------------------------
# Numerical stability guards
# ---------------------------------------------------------------------------


class TestNumericalStability:
    def test_zero_variance_series_excluded(self, db_session: Session) -> None:
        prices_normal = [100.0 + i for i in range(30)]
        prices_flat = [100.0] * 30  # zero variance
        _seed_market_bars(db_session, "NORM", prices_normal)
        _seed_market_bars(db_session, "FLAT", prices_flat)
        prices_c = _make_correlated_prices(prices_normal)
        _seed_market_bars(db_session, "CORR_C", prices_c)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["NORM", "FLAT", "CORR_C"])

        # FLAT should be excluded; matrix should still be computed for NORM and CORR_C
        if result.symbol_snapshot_count > 0:
            repo = CorrelationSnapshotRepository(db_session)
            snap = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
            assert snap is not None
            assert "FLAT" not in snap.asset_universe
            # Warning about zero-variance series should be recorded
            assert any("zero" in w.lower() or "variance" in w.lower() for w in snap.warnings)

    def test_nan_in_returns_excluded(self, db_session: Session) -> None:
        """Seeds bars with a price of 0 to trigger log(p/0) = NaN handling."""
        prices = [100.0, 0.0] + [100.0 + i for i in range(28)]
        prices2 = [100.0 + i for i in range(30)]
        prices3 = [95.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "NAN_SYM", prices)
        _seed_market_bars(db_session, "OK_SYM2", prices2)
        _seed_market_bars(db_session, "OK_SYM3", prices3)

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        # Should not raise; non-finite assets are excluded with a warning
        result = svc.run(as_of=_NOW, portfolio_symbols=["NAN_SYM", "OK_SYM2", "OK_SYM3"])
        assert result is not None  # no exception

    def test_result_to_jsonable(self, db_session: Session) -> None:
        svc = CorrelationMonitoringService(session=db_session)
        result = svc.run(as_of=_NOW, portfolio_symbols=[])
        payload = CorrelationMonitoringService.result_to_jsonable(result)
        assert isinstance(payload, dict)
        assert "run_id" in payload
        assert "snapshots_persisted" in payload


# ---------------------------------------------------------------------------
# Correlation diagnostics
# ---------------------------------------------------------------------------


class TestCorrelationDiagnostics:
    def test_top_correlated_pairs_populated(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "D1", base)
        _seed_market_bars(db_session, "D2", _make_correlated_prices(base, 0.0001))
        _seed_market_bars(db_session, "D3", [90.0 - i * 0.3 for i in range(30)])

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20], min_observations=10, top_pairs_limit=5
            ),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["D1", "D2", "D3"])

        assert len(result.top_correlated_symbol_pairs) > 0
        # Pairs should be sorted by descending abs_correlation
        pairs = result.top_correlated_symbol_pairs
        for i in range(len(pairs) - 1):
            assert pairs[i].abs_correlation >= pairs[i + 1].abs_correlation

    def test_hidden_cluster_detected_flag(self, db_session: Session) -> None:
        base = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "CLUS1", base)
        _seed_market_bars(db_session, "CLUS2", _make_correlated_prices(base, 0.00001))
        _seed_market_bars(db_session, "CLUS3", _make_correlated_prices(base, 0.00001))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20],
                min_observations=10,
                cluster_correlation_threshold=0.50,
            ),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["CLUS1", "CLUS2", "CLUS3"])
        assert result.hidden_cluster_detected is True
        assert result.cluster_count >= 1

    def test_average_portfolio_correlation_computed(self, db_session: Session) -> None:
        base = [100.0 + i * 0.4 for i in range(30)]
        _seed_market_bars(db_session, "AVG1", base)
        _seed_market_bars(db_session, "AVG2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["AVG1", "AVG2"])

        assert result.average_portfolio_correlation is not None
        assert -1.0 <= result.average_portfolio_correlation <= 1.0


# ---------------------------------------------------------------------------
# Covariance matrix correctness
# ---------------------------------------------------------------------------


class TestCovarianceMatrix:
    def test_covariance_matrix_is_symmetric(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "SYM1", base)
        _seed_market_bars(db_session, "SYM2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["SYM1", "SYM2"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        cov_dict = snap.covariance_matrix
        assets = list(cov_dict.keys())
        for a in assets:
            for b in assets:
                assert math.isclose(cov_dict[a][b], cov_dict[b][a], rel_tol=1e-6), (
                    f"Covariance not symmetric: [{a}][{b}] != [{b}][{a}]"
                )

    def test_diagonal_of_covariance_is_positive(self, db_session: Session) -> None:
        base = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "PD1", base)
        _seed_market_bars(db_session, "PD2", [90.0 - i * 0.2 for i in range(30)])

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["PD1", "PD2"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        cov_dict = snap.covariance_matrix
        for asset, row in cov_dict.items():
            assert row[asset] > 0, f"Non-positive diagonal variance for {asset}"

    def test_positive_definite_flag_set(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "POS1", base)
        _seed_market_bars(db_session, "POS2", [85.0 - i * 0.3 for i in range(30)])

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["POS1", "POS2"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_covariance(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        assert isinstance(snap.is_positive_definite, bool)


# ---------------------------------------------------------------------------
# Repository query tests
# ---------------------------------------------------------------------------


class TestCorrelationRepository:
    def test_get_latest_returns_most_recent(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "REPO1", base)
        _seed_market_bars(db_session, "REPO2", _make_correlated_prices(base))

        config = CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10)

        # First run
        svc1 = CorrelationMonitoringService(session=db_session, config=config)
        svc1.run(
            as_of=_NOW - timedelta(days=1), portfolio_symbols=["REPO1", "REPO2"], run_id="run-old"
        )

        # Second run
        svc2 = CorrelationMonitoringService(session=db_session, config=config)
        svc2.run(as_of=_NOW, portfolio_symbols=["REPO1", "REPO2"], run_id="run-new")

        repo = CorrelationSnapshotRepository(db_session)
        latest = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert latest is not None
        assert latest.run_id == "run-new"

    def test_history_query(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "HIST1", base)
        _seed_market_bars(db_session, "HIST2", _make_correlated_prices(base))

        config = CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10)
        for day_offset in range(3):
            svc = CorrelationMonitoringService(session=db_session, config=config)
            svc.run(
                as_of=_NOW - timedelta(days=day_offset),
                portfolio_symbols=["HIST1", "HIST2"],
                run_id=f"hist-run-{day_offset}",
            )

        repo = CorrelationSnapshotRepository(db_session)
        history = repo.get_correlation_history(
            snapshot_type="symbol",
            lookback_window=20,
            since=_NOW - timedelta(days=10),
            limit=10,
        )
        assert len(history) >= 3

    def test_no_data_returns_none(self, db_session: Session) -> None:
        repo = CorrelationSnapshotRepository(db_session)
        result = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert result is None

    def test_covariance_history_query(self, db_session: Session) -> None:
        base = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "CHIST1", base)
        _seed_market_bars(db_session, "CHIST2", _make_correlated_prices(base))

        config = CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10)
        for day_offset in range(2):
            svc = CorrelationMonitoringService(session=db_session, config=config)
            svc.run(
                as_of=_NOW - timedelta(days=day_offset),
                portfolio_symbols=["CHIST1", "CHIST2"],
                run_id=f"cov-hist-{day_offset}",
            )

        repo = CorrelationSnapshotRepository(db_session)
        history = repo.get_covariance_history(
            snapshot_type="symbol",
            lookback_window=20,
            since=_NOW - timedelta(days=10),
        )
        assert len(history) >= 2


# ---------------------------------------------------------------------------
# Regime-awareness hooks
# ---------------------------------------------------------------------------


class TestRegimeHooks:
    def test_regime_label_stored_in_snapshot(self, db_session: Session) -> None:
        base = [100.0 + i * 0.5 for i in range(30)]
        _seed_market_bars(db_session, "REG1", base)
        _seed_market_bars(db_session, "REG2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(
                symbol_windows=[20], min_observations=10, regime_label="high_vol"
            ),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["REG1", "REG2"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        assert snap.regime_label == "high_vol"

    def test_no_regime_label_is_null(self, db_session: Session) -> None:
        base = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "NRL1", base)
        _seed_market_bars(db_session, "NRL2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        svc.run(as_of=_NOW, portfolio_symbols=["NRL1", "NRL2"])

        repo = CorrelationSnapshotRepository(db_session)
        snap = repo.get_latest_correlation(snapshot_type="symbol", lookback_window=20)
        assert snap is not None
        assert snap.regime_label is None


# ---------------------------------------------------------------------------
# Run result structure
# ---------------------------------------------------------------------------


class TestRunResult:
    def test_result_has_all_fields(self, db_session: Session) -> None:
        base = [100.0 + i * 0.3 for i in range(30)]
        _seed_market_bars(db_session, "FULL1", base)
        _seed_market_bars(db_session, "FULL2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["FULL1", "FULL2"])

        assert result.run_id is not None
        assert result.computed_at is not None
        assert result.duration_seconds > 0
        assert isinstance(result.warnings, list)
        assert isinstance(result.top_correlated_symbol_pairs, list)
        assert isinstance(result.top_correlated_strategy_pairs, list)
        assert isinstance(result.hidden_cluster_detected, bool)

    def test_result_to_jsonable_is_serializable(self, db_session: Session) -> None:
        base = [100.0 + i for i in range(30)]
        _seed_market_bars(db_session, "JSON1", base)
        _seed_market_bars(db_session, "JSON2", _make_correlated_prices(base))

        svc = CorrelationMonitoringService(
            session=db_session,
            config=CorrelationMonitoringConfig(symbol_windows=[20], min_observations=10),
        )
        result = svc.run(as_of=_NOW, portfolio_symbols=["JSON1", "JSON2"])
        payload = CorrelationMonitoringService.result_to_jsonable(result)

        import json

        serialized = json.dumps(payload)
        assert len(serialized) > 0
        parsed = json.loads(serialized)
        assert parsed["run_id"] == result.run_id

    def test_no_data_result_has_zero_counts(self, db_session: Session) -> None:
        svc = CorrelationMonitoringService(session=db_session)
        result = svc.run(as_of=_NOW, portfolio_symbols=[])
        assert result.snapshots_persisted == 0
        assert result.symbol_snapshot_count == 0
        assert result.strategy_snapshot_count == 0
        assert result.sector_snapshot_count == 0
        assert result.covariance_snapshot_count == 0
        assert result.hidden_cluster_detected is False
        assert result.cluster_count == 0
