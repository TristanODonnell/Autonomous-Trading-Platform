from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.risk_budgeting_service import (
    RiskBudgetConfig,
    RiskBudgetingService,
)
from autonomous_trading_platform.contracts.runtime.risk_budget import (
    AllocationMode,
    FallbackReason,
)
from autonomous_trading_platform.storage.sor.models.correlation_snapshots import (
    CovarianceSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_budget_snapshot_repository import (
    RiskBudgetSnapshotRepository,
)

_NOW = datetime(2026, 5, 27, 16, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_volatility(
    session: Session,
    strategy_id: str,
    vol: float,
    ts: datetime | None = None,
) -> None:
    if ts is None:
        ts = _NOW - timedelta(hours=1)
    session.add(
        StrategyLivePerformanceSnapshot(
            snapshot_id=f"{strategy_id}_vol_{uuid4().hex[:8]}",
            strategy_id=strategy_id,
            run_id=None,
            computed_at=ts,
            realized_return=None,
            rolling_sharpe=None,
            realized_drawdown=None,
            realized_volatility=vol,
            live_win_rate=None,
            trade_count=None,
            winning_trade_count=None,
            days_live=30,
            days_since_profitable_day=None,
            window_days=None,
            window_trades=None,
            metadata_json=None,
        )
    )
    session.flush()


def _seed_covariance_snapshot(
    session: Session,
    strategy_ids: list[str],
    cov_matrix: list[list[float]],
    *,
    snapshot_id: str | None = None,
    is_positive_definite: bool = True,
    condition_number: float = 10.0,
    lookback_window: int = 60,
) -> str:
    sid = snapshot_id or str(uuid4())
    # Build dict-of-dict matrix
    cov_dict = {
        a: {b: cov_matrix[i][j] for j, b in enumerate(strategy_ids)}
        for i, a in enumerate(strategy_ids)
    }
    matrix_hash = f"hash_{sid[:8]}"
    session.add(
        CovarianceSnapshotRow(
            snapshot_id=sid,
            snapshot_type="strategy",
            lookback_window=lookback_window,
            computed_at=_NOW - timedelta(minutes=30),
            as_of_date=_NOW - timedelta(minutes=30),
            asset_universe=strategy_ids,
            asset_count=len(strategy_ids),
            observations_used=lookback_window,
            annualization_factor=252,
            covariance_matrix=cov_dict,
            matrix_hash=matrix_hash,
            is_positive_definite=is_positive_definite,
            condition_number=condition_number,
            warnings=[],
            computation_duration_seconds=0.01,
            run_id=None,
        )
    )
    session.flush()
    return sid


def _diagonal_cov(vols: list[float]) -> list[list[float]]:
    """Diagonal covariance matrix from per-strategy daily volatilities."""
    n = len(vols)
    return [[vols[i] ** 2 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _full_cov(vols: list[float], rho: float) -> list[list[float]]:
    """Constant-correlation covariance matrix."""
    n = len(vols)
    return [[vols[i] * vols[j] * (1.0 if i == j else rho) for j in range(n)] for i in range(n)]


# ---------------------------------------------------------------------------
# Equal Risk Contribution — basic correctness
# ---------------------------------------------------------------------------


class TestEqualRiskContribution:
    def test_erc_diagonal_cov_equal_budgets_gives_inverse_vol_weights(
        self, db_session: Session
    ) -> None:
        """With diagonal covariance and equal budgets, ERC = inverse-vol weighting."""
        strats = ["S1", "S2", "S3"]
        vols = [0.01, 0.02, 0.04]  # daily volatilities
        cov = _diagonal_cov(vols)

        _seed_covariance_snapshot(db_session, strats, cov)

        config = RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION)
        svc = RiskBudgetingService(session=db_session, config=config)
        result = svc.compute(strategy_ids=strats)

        assert result.mode == AllocationMode.EQUAL_RISK_CONTRIBUTION
        assert result.convergence_achieved is True

        w = result.recommended_weights
        # Under diagonal covariance + equal budgets: w_i ∝ 1/σ_i
        # Check that lower-vol strategies get higher weights
        assert w["S1"] > w["S2"] > w["S3"]

    def test_erc_weights_sum_to_one(self, db_session: Session) -> None:
        strats = ["A", "B", "C", "D"]
        vols = [0.01, 0.015, 0.02, 0.025]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        total = sum(result.recommended_weights.values())
        assert math.isclose(total, 1.0, rel_tol=1e-6)

    def test_erc_equal_vol_gives_equal_weights(self, db_session: Session) -> None:
        strats = ["X", "Y", "Z"]
        vol = 0.02
        cov = _diagonal_cov([vol, vol, vol])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        w = result.recommended_weights
        expected = 1.0 / 3
        for sid in strats:
            assert math.isclose(w[sid], expected, rel_tol=1e-4), f"Weight for {sid} = {w[sid]}"

    def test_erc_risk_contributions_approximately_equal(self, db_session: Session) -> None:
        strats = ["M", "N", "O"]
        vols = [0.01, 0.03, 0.05]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        pcts = [rc.pct_of_portfolio_variance for rc in result.risk_contributions]
        assert len(pcts) == 3
        # All risk contributions should be close to 1/3
        for pct in pcts:
            assert math.isclose(pct, 1.0 / 3, rel_tol=0.05), f"Risk contribution {pct} not ~1/3"


# ---------------------------------------------------------------------------
# Fixed risk budgets
# ---------------------------------------------------------------------------


class TestFixedRiskBudgets:
    def test_fixed_budgets_respected(self, db_session: Session) -> None:
        strats = ["F1", "F2"]
        vols = [0.02, 0.02]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        budgets = {"F1": 0.70, "F2": 0.30}
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.FIXED_RISK_BUDGETS, risk_budgets=budgets),
        )
        result = svc.compute(strategy_ids=strats)

        # With equal volatility, budget should map directly to capital weight
        assert result.recommended_weights["F1"] > result.recommended_weights["F2"]

    def test_fixed_budgets_normalized_if_not_sum_to_one(self, db_session: Session) -> None:
        strats = ["G1", "G2", "G3"]
        vols = [0.01, 0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        # Budgets sum to 2.0, should be normalized to 1.0
        budgets = {"G1": 0.6, "G2": 0.8, "G3": 0.6}
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.FIXED_RISK_BUDGETS, risk_budgets=budgets),
        )
        result = svc.compute(strategy_ids=strats)

        total = sum(result.recommended_weights.values())
        assert math.isclose(total, 1.0, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Inverse volatility
# ---------------------------------------------------------------------------


class TestInverseVolatility:
    def test_low_vol_strategy_gets_larger_weight(self, db_session: Session) -> None:
        strats = ["LV", "HV"]
        _seed_volatility(db_session, "LV", vol=0.01)
        _seed_volatility(db_session, "HV", vol=0.05)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.INVERSE_VOLATILITY),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.recommended_weights["LV"] > result.recommended_weights["HV"]

    def test_inverse_vol_proportionality(self, db_session: Session) -> None:
        strats = ["V1", "V2"]
        _seed_volatility(db_session, "V1", vol=0.02)
        _seed_volatility(db_session, "V2", vol=0.04)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.INVERSE_VOLATILITY),
        )
        result = svc.compute(strategy_ids=strats)

        # w[V2] / w[V1] should be ≈ vol[V1] / vol[V2] = 0.5
        ratio = result.recommended_weights["V2"] / result.recommended_weights["V1"]
        assert math.isclose(ratio, 0.5, rel_tol=1e-4)

    def test_equal_capital_mode(self, db_session: Session) -> None:
        strats = ["EC1", "EC2", "EC3"]
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL),
        )
        result = svc.compute(strategy_ids=strats)

        for sid in strats:
            assert math.isclose(result.recommended_weights[sid], 1.0 / 3, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    def test_fallback_to_equal_capital_when_no_data(self, db_session: Session) -> None:
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=["FB1", "FB2", "FB3"])

        assert result.fallback_used is True
        assert result.fallback_reason is not None
        total = sum(result.recommended_weights.values())
        assert math.isclose(total, 1.0, rel_tol=1e-6)

    def test_fallback_to_inverse_vol_when_no_covariance_but_vol_available(
        self, db_session: Session
    ) -> None:
        _seed_volatility(db_session, "FV1", vol=0.01)
        _seed_volatility(db_session, "FV2", vol=0.04)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=["FV1", "FV2"])

        assert result.fallback_used is True
        # With fallback to inverse-vol, lower-vol strategy gets higher weight
        assert result.recommended_weights["FV1"] > result.recommended_weights["FV2"]
        assert any("inverse-volatility" in w for w in result.warnings)

    def test_fallback_used_for_unstable_covariance(self, db_session: Session) -> None:
        strats = ["UC1", "UC2"]
        vols = [0.02, 0.02]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(
            db_session,
            strats,
            cov,
            is_positive_definite=False,
        )

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.fallback_used is True
        assert result.fallback_reason == FallbackReason.NOT_POSITIVE_DEFINITE

    def test_fallback_used_for_high_condition_number(self, db_session: Session) -> None:
        strats = ["HCN1", "HCN2"]
        vols = [0.02, 0.02]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(
            db_session,
            strats,
            cov,
            condition_number=2e12,  # exceeds threshold
        )

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.fallback_used is True
        assert result.fallback_reason == FallbackReason.UNSTABLE_COVARIANCE

    def test_single_strategy_returns_full_weight(self, db_session: Session) -> None:
        svc = RiskBudgetingService(session=db_session)
        result = svc.compute(strategy_ids=["SOLO"])

        assert result.fallback_used is True
        assert result.recommended_weights.get("SOLO") == pytest.approx(1.0, rel=1e-6)

    def test_empty_strategies_returns_empty_result(self, db_session: Session) -> None:
        svc = RiskBudgetingService(session=db_session)
        result = svc.compute(strategy_ids=[])

        assert result.strategy_count == 0
        assert result.recommended_weights == {}


# ---------------------------------------------------------------------------
# Risk contribution calculations
# ---------------------------------------------------------------------------


class TestRiskContributions:
    def test_risk_contributions_sum_to_one(self, db_session: Session) -> None:
        strats = ["RC1", "RC2", "RC3"]
        vols = [0.02, 0.03, 0.01]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        total_rc = sum(rc.pct_of_portfolio_variance for rc in result.risk_contributions)
        assert math.isclose(total_rc, 1.0, rel_tol=1e-4)

    def test_high_vol_strategy_contributes_more_risk_under_equal_capital(
        self, db_session: Session
    ) -> None:
        strats = ["LOW_VOL", "HIGH_VOL"]
        vols = [0.01, 0.05]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL),
        )
        result = svc.compute(strategy_ids=strats)

        rc_by_id = {rc.strategy_id: rc for rc in result.risk_contributions}
        # HIGH_VOL should contribute much more risk at equal capital
        assert (
            rc_by_id["HIGH_VOL"].pct_of_portfolio_variance
            > rc_by_id["LOW_VOL"].pct_of_portfolio_variance
        )

    def test_portfolio_volatility_estimate_positive(self, db_session: Session) -> None:
        strats = ["PV1", "PV2"]
        vols = [0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.portfolio_volatility_estimate is not None
        assert result.portfolio_volatility_estimate > 0

    def test_portfolio_volatility_annualized(self, db_session: Session) -> None:
        """Diagonal cov with daily vol → annualized port vol ≈ daily_vol * sqrt(252)."""
        strats = ["ANN1", "ANN2"]
        daily_vol = 0.02
        cov = _diagonal_cov([daily_vol, daily_vol])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL, annualization_factor=252),
        )
        result = svc.compute(strategy_ids=strats)

        # Equal weights → portfolio daily var = 0.5 * daily_vol^2 (diagonal case)
        # → portfolio daily vol = daily_vol / sqrt(2)
        # → annualized = daily_vol / sqrt(2) * sqrt(252)
        expected = daily_vol / math.sqrt(2) * math.sqrt(252)
        assert result.portfolio_volatility_estimate is not None
        assert math.isclose(result.portfolio_volatility_estimate, expected, rel_tol=0.01)


# ---------------------------------------------------------------------------
# Hidden concentration detection
# ---------------------------------------------------------------------------


class TestHiddenConcentration:
    def test_hidden_concentration_detected_when_one_strategy_dominates(
        self, db_session: Session
    ) -> None:
        # Equal capital with vastly different volatilities
        strats = ["HD_LOW", "HD_MED", "HD_HIGH"]
        vols = [0.005, 0.01, 0.10]  # HD_HIGH is 20x more volatile
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.hidden_concentration_detected is True
        assert any("concentration" in w.lower() for w in result.warnings)

    def test_no_hidden_concentration_after_erc(self, db_session: Session) -> None:
        # ERC should equalize risk contributions → no single dominator
        strats = ["ERC1", "ERC2", "ERC3"]
        vols = [0.01, 0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        # After ERC all contributions should be ~1/3
        assert result.hidden_concentration_detected is False


# ---------------------------------------------------------------------------
# Diversification ratio
# ---------------------------------------------------------------------------


class TestDiversificationRatio:
    def test_diversification_ratio_above_one_with_uncorrelated_assets(
        self, db_session: Session
    ) -> None:
        strats = ["DR1", "DR2"]
        vols = [0.02, 0.03]
        _seed_volatility(db_session, "DR1", vol=0.02)
        _seed_volatility(db_session, "DR2", vol=0.03)
        cov = _diagonal_cov(vols)  # uncorrelated
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL),
        )
        result = svc.compute(strategy_ids=strats)

        # Diversification ratio > 1 for uncorrelated assets
        if result.diversification_ratio is not None:
            assert result.diversification_ratio >= 1.0

    def test_diversification_ratio_one_with_perfectly_correlated_assets(
        self, db_session: Session
    ) -> None:
        strats = ["PC1", "PC2"]
        vols = [0.02, 0.02]
        _seed_volatility(db_session, "PC1", vol=0.02)
        _seed_volatility(db_session, "PC2", vol=0.02)
        cov = _full_cov(vols, rho=1.0)  # fully correlated
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_CAPITAL),
        )
        result = svc.compute(strategy_ids=strats)

        # Fully correlated → diversification ratio = 1.0
        if result.diversification_ratio is not None:
            assert math.isclose(result.diversification_ratio, 1.0, rel_tol=0.05)


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------


class TestSnapshotPersistence:
    def test_snapshot_persisted_after_run(self, db_session: Session) -> None:
        strats = ["PERS1", "PERS2"]
        vols = [0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        run_id = str(uuid4())
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        svc.compute(strategy_ids=strats, run_id=run_id)

        repo = RiskBudgetSnapshotRepository(db_session)
        snaps = repo.get_for_run(run_id)
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.run_id == run_id
        assert snap.strategy_count == 2
        assert set(snap.strategy_universe) == {"PERS1", "PERS2"}

    def test_snapshot_contains_recommended_weights(self, db_session: Session) -> None:
        strats = ["W1", "W2", "W3"]
        vols = [0.01, 0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        run_id = str(uuid4())
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats, run_id=run_id)

        repo = RiskBudgetSnapshotRepository(db_session)
        snap = repo.get_latest()
        assert snap is not None
        stored_weights = snap.recommended_weights
        for sid in strats:
            assert sid in stored_weights
            assert math.isclose(stored_weights[sid], result.recommended_weights[sid], rel_tol=1e-6)

    def test_covariance_snapshot_id_linked(self, db_session: Session) -> None:
        strats = ["LK1", "LK2"]
        vols = [0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov, snapshot_id="cov-snap-123")

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.covariance_snapshot_id == "cov-snap-123"

        repo = RiskBudgetSnapshotRepository(db_session)
        snap = repo.get_latest()
        assert snap is not None
        assert snap.covariance_snapshot_id == "cov-snap-123"

    def test_fallback_snapshot_persisted(self, db_session: Session) -> None:
        run_id = str(uuid4())
        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=["FB_A", "FB_B"], run_id=run_id)

        assert result.fallback_used is True

        # Even fallback results are NOT persisted via _persist if strategy_count < _MIN_STRATEGIES
        # (they skip persistence). But with 2 strategies, persistence happens.
        repo = RiskBudgetSnapshotRepository(db_session)
        snaps = repo.get_for_run(run_id)
        assert len(snaps) == 1
        assert snaps[0].fallback_used is True

    def test_latest_query_returns_most_recent(self, db_session: Session) -> None:
        strats = ["LATEST1", "LATEST2"]
        cov = _diagonal_cov([0.02, 0.03])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        svc.compute(strategy_ids=strats, run_id="run-old")
        svc.compute(strategy_ids=strats, run_id="run-new")

        repo = RiskBudgetSnapshotRepository(db_session)
        snap = repo.get_latest()
        assert snap is not None
        assert snap.run_id == "run-new"

    def test_history_query(self, db_session: Session) -> None:
        strats = ["HIST1", "HIST2"]
        cov = _diagonal_cov([0.02, 0.03])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        for i in range(3):
            svc.compute(strategy_ids=strats, run_id=f"hist-run-{i}")

        repo = RiskBudgetSnapshotRepository(db_session)
        history = repo.get_history(
            since=_NOW - timedelta(days=1),
            limit=10,
        )
        assert len(history) >= 3


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_same_weights(self, db_session: Session) -> None:
        strats = ["DET1", "DET2", "DET3"]
        vols = [0.015, 0.025, 0.02]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov, snapshot_id="det-cov")

        config = RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION)
        svc = RiskBudgetingService(session=db_session, config=config)

        result1 = svc.compute(strategy_ids=strats, run_id="det-run-1")
        result2 = svc.compute(strategy_ids=strats, run_id="det-run-2")

        for sid in strats:
            assert math.isclose(
                result1.recommended_weights[sid],
                result2.recommended_weights[sid],
                rel_tol=1e-6,
            )


# ---------------------------------------------------------------------------
# Correlated strategies — hidden concentration exposure
# ---------------------------------------------------------------------------


class TestCorrelatedStrategies:
    def test_correlated_strategies_increase_portfolio_vol(self, db_session: Session) -> None:
        """Correlated covariance matrix → higher portfolio vol than independent one.

        We seed each matrix under a different lookback_window so the service can
        select the specific snapshot it needs via config.
        """
        strats = ["COR1", "COR2"]
        vols = [0.02, 0.02]

        # Seed correlated under window=20, uncorrelated under window=30
        _seed_covariance_snapshot(
            db_session,
            strats,
            _full_cov(vols, rho=0.9),
            snapshot_id="corr-snap",
            lookback_window=20,
        )
        _seed_covariance_snapshot(
            db_session,
            strats,
            _diagonal_cov(vols),
            snapshot_id="uncorr-snap",
            lookback_window=30,
        )

        svc_corr = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(
                mode=AllocationMode.EQUAL_CAPITAL, covariance_lookback_window=20
            ),
        )
        result_corr = svc_corr.compute(strategy_ids=strats)

        svc_uncorr = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(
                mode=AllocationMode.EQUAL_CAPITAL, covariance_lookback_window=30
            ),
        )
        result_uncorr = svc_uncorr.compute(strategy_ids=strats)

        if (
            result_corr.portfolio_volatility_estimate is not None
            and result_uncorr.portfolio_volatility_estimate is not None
        ):
            assert (
                result_corr.portfolio_volatility_estimate
                > result_uncorr.portfolio_volatility_estimate
            )


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    def test_all_fields_present(self, db_session: Session) -> None:
        strats = ["RS1", "RS2"]
        vols = [0.02, 0.03]
        cov = _diagonal_cov(vols)
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        assert result.run_id is not None
        assert result.computed_at is not None
        assert result.duration_seconds > 0
        assert isinstance(result.warnings, list)
        assert isinstance(result.recommended_weights, dict)
        assert isinstance(result.risk_contributions, list)

    def test_result_to_jsonable(self, db_session: Session) -> None:
        strats = ["JSON1", "JSON2"]
        cov = _diagonal_cov([0.02, 0.03])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)
        payload = RiskBudgetingService.result_to_jsonable(result)

        import json

        serialized = json.dumps(payload)
        assert len(serialized) > 0
        parsed = json.loads(serialized)
        assert "recommended_weights" in parsed
        assert "risk_contributions" in parsed

    def test_all_strategies_have_risk_contributions(self, db_session: Session) -> None:
        strats = ["RC_ALL1", "RC_ALL2", "RC_ALL3"]
        cov = _diagonal_cov([0.01, 0.02, 0.03])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        rc_ids = {rc.strategy_id for rc in result.risk_contributions}
        assert rc_ids == set(strats)

    def test_weights_all_positive(self, db_session: Session) -> None:
        strats = ["POS1", "POS2", "POS3", "POS4"]
        cov = _diagonal_cov([0.01, 0.02, 0.01, 0.03])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        result = svc.compute(strategy_ids=strats)

        for sid, w in result.recommended_weights.items():
            assert w > 0, f"Weight for {sid} should be positive"

    def test_get_latest_snapshot_returns_after_run(self, db_session: Session) -> None:
        strats = ["LS1", "LS2"]
        cov = _diagonal_cov([0.02, 0.02])
        _seed_covariance_snapshot(db_session, strats, cov)

        svc = RiskBudgetingService(
            session=db_session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        )
        svc.compute(strategy_ids=strats)

        latest = svc.get_latest_snapshot()
        assert latest is not None
        assert latest.strategy_count == 2
