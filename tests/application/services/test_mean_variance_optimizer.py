from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.mean_variance_optimizer import (
    InfeasibleConstraintsError,
    MeanVarianceConfig,
    MeanVarianceOptimizer,
    _NumpyQPSolver,
    _QpProblem,
)
from autonomous_trading_platform.contracts.runtime.optimizer import (
    FallbackMode,
    OptimizationObjective,
    OptimizerConstraints,
    SolverStatus,
)
from autonomous_trading_platform.storage.sor.models.correlation_snapshots import (
    CovarianceSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.optimizer_run_repository import (
    OptimizerRunRepository,
)

_NOW = datetime(2026, 5, 27, 16, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_covariance(
    session: Session,
    assets: list[str],
    cov_matrix: list[list[float]],
    *,
    snapshot_id: str | None = None,
    is_positive_definite: bool = True,
    condition_number: float = 5.0,
    snapshot_type: str = "symbol",
    lookback_window: int = 60,
) -> str:
    sid = snapshot_id or str(uuid4())
    cov_dict = {
        a: {b: cov_matrix[i][j] for j, b in enumerate(assets)} for i, a in enumerate(assets)
    }
    session.add(
        CovarianceSnapshotRow(
            snapshot_id=sid,
            snapshot_type=snapshot_type,
            lookback_window=lookback_window,
            computed_at=_NOW - timedelta(minutes=10),
            as_of_date=_NOW - timedelta(minutes=10),
            asset_universe=assets,
            asset_count=len(assets),
            observations_used=lookback_window,
            annualization_factor=252,
            covariance_matrix=cov_dict,
            matrix_hash=f"hash_{sid[:6]}",
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
    n = len(vols)
    return [[vols[i] ** 2 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _full_cov(vols: list[float], rho: float) -> list[list[float]]:
    n = len(vols)
    return [[vols[i] * vols[j] * (1.0 if i == j else rho) for j in range(n)] for i in range(n)]


def _equal_returns(n: int, value: float = 0.10) -> dict:
    return {f"A{i}": value for i in range(n)}


def _build_optimizer(
    session: Session,
    *,
    dry_run: bool = True,
    risk_aversion: float = 1.0,
    fallback_mode: FallbackMode = FallbackMode.EQUAL_WEIGHTS,
    snapshot_type: str = "symbol",
) -> MeanVarianceOptimizer:
    config = MeanVarianceConfig(
        dry_run=dry_run,
        risk_aversion=risk_aversion,
        fallback_mode=fallback_mode,
        covariance_snapshot_type=snapshot_type,
    )
    return MeanVarianceOptimizer(session=session, config=config)


# ---------------------------------------------------------------------------
# QP Solver unit tests (pure numpy, no DB)
# ---------------------------------------------------------------------------


class TestNumpyQPSolver:
    def _make_problem(
        self,
        n: int = 3,
        lam: float = 0.0,
        vols: list[float] | None = None,
    ) -> _QpProblem:
        if vols is None:
            vols = [0.01 + 0.01 * i for i in range(n)]
        cov = np.diag([v**2 for v in vols])
        mu = np.array([0.05 + 0.02 * i for i in range(n)])
        lb = np.zeros(n)
        ub = np.ones(n)
        p = _QpProblem(
            cov=cov,
            mu=mu,
            lb=lb,
            ub=ub,
            risk_aversion=lam,
            sector_indices={},
            sector_caps={},
            current_weights=None,
            turnover_penalty=0.0,
            regularization=1e-8,
        )
        p.pgd_max_iter = 3000  # type: ignore[attr-defined]
        p.pgd_tol = 1e-9  # type: ignore[attr-defined]
        return p

    def test_weights_sum_to_one(self) -> None:
        solver = _NumpyQPSolver()
        sol = solver.solve(self._make_problem(3))
        assert math.isclose(float(sol.weights.sum()), 1.0, rel_tol=1e-6)

    def test_long_only_by_default(self) -> None:
        solver = _NumpyQPSolver()
        sol = solver.solve(self._make_problem(4))
        assert np.all(sol.weights >= -1e-9)

    def test_min_variance_low_vol_gets_more_weight(self) -> None:
        solver = _NumpyQPSolver()
        vols = [0.01, 0.05]
        sol = solver.solve(self._make_problem(2, vols=vols))
        # Lower-vol asset should receive more weight at minimum variance
        assert sol.weights[0] > sol.weights[1]

    def test_sector_cap_enforced(self) -> None:
        solver = _NumpyQPSolver()
        p = self._make_problem(4)
        # Sectors: A0, A1 in sector X with cap 0.4
        p.sector_indices = {"X": [0, 1]}
        p.sector_caps = {"X": 0.40}
        sol = solver.solve(p)
        sector_weight = float(sol.weights[0] + sol.weights[1])
        assert sector_weight <= 0.40 + 1e-5

    def test_infeasible_lb_raises(self) -> None:
        solver = _NumpyQPSolver()
        p = self._make_problem(2)
        p.lb = np.array([0.6, 0.6])  # sum > 1
        with pytest.raises(InfeasibleConstraintsError):
            solver.solve(p)

    def test_infeasible_ub_raises(self) -> None:
        solver = _NumpyQPSolver()
        p = self._make_problem(2)
        p.ub = np.array([0.3, 0.3])  # sum < 1
        with pytest.raises(InfeasibleConstraintsError):
            solver.solve(p)

    def test_box_simplex_projection_feasibility(self) -> None:
        from autonomous_trading_platform.application.services.mean_variance_optimizer import (
            _NumpyQPSolver,
        )

        solver = _NumpyQPSolver()
        v = np.array([0.8, 0.1, 0.1])
        lb = np.array([0.1, 0.1, 0.1])
        ub = np.array([0.5, 0.5, 0.5])
        w = solver._project_box_simplex(v, lb, ub)
        assert math.isclose(float(w.sum()), 1.0, rel_tol=1e-6)
        assert np.all(w >= lb - 1e-10)
        assert np.all(w <= ub + 1e-10)


# ---------------------------------------------------------------------------
# Minimum variance objective
# ---------------------------------------------------------------------------


class TestMinimumVariance:
    def test_min_var_lower_variance_than_equal_weight(self, db_session: Session) -> None:
        assets = ["A0", "A1", "A2"]
        vols = [0.01, 0.05, 0.10]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        opt = _build_optimizer(db_session)
        result = opt.optimize(
            assets=assets,
            expected_returns=_equal_returns(3, 0.1),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )

        assert result.solver_status in (SolverStatus.OPTIMAL, SolverStatus.SUBOPTIMAL)
        # Equal weights portfolio variance
        w_eq = np.ones(3) / 3
        cov = np.diag([v**2 for v in vols])
        eq_var = float(w_eq @ cov @ w_eq)
        # Optimized variance must be ≤ equal weight variance
        assert result.expected_volatility is not None
        assert result.expected_volatility**2 <= eq_var + 1e-6

    def test_min_var_weights_sum_to_one(self, db_session: Session) -> None:
        assets = ["B0", "B1", "B2", "B3"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03, 0.01, 0.04]))

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(4),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        total = sum(result.target_weights.values())
        assert math.isclose(total, 1.0, rel_tol=1e-5)

    def test_min_var_long_only(self, db_session: Session) -> None:
        assets = ["C0", "C1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns={"C0": 0.05, "C1": 0.15},
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        for w in result.target_weights.values():
            assert w >= -1e-9

    def test_min_var_low_vol_gets_largest_weight(self, db_session: Session) -> None:
        assets = ["LV", "MV", "HV"]
        vols = [0.01, 0.03, 0.08]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns={"LV": 0.1, "MV": 0.1, "HV": 0.1},
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        w = result.target_weights
        assert w["LV"] > w["MV"] > w["HV"]


# ---------------------------------------------------------------------------
# Maximum utility objective
# ---------------------------------------------------------------------------


class TestMaximumUtility:
    def test_higher_risk_aversion_reduces_expected_return(self, db_session: Session) -> None:
        """Conventional interpretation: high risk_aversion → λ small → near min-variance.

        Use two extreme risk_aversion values with assets where the risk-return
        tradeoff is sharp enough that the two regimes produce clearly different
        corner solutions.  Assets: LR (1% vol, 5% return) vs HR (8% vol, 20% return).
        λ = 1/risk_aversion.  The minimum-variance solution concentrates in LR;
        the return-seeking solution concentrates in HR.
        """
        assets = ["LR", "HR"]
        # diagonal cov: daily-scale but numeric check uses annual scaling from AnnFactor=252
        vols = [0.01, 0.08]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))
        mu = {"LR": 0.05, "HR": 0.20}

        # Very low risk aversion → λ=1/0.001=1000 → return-seeking → concentrates in HR
        opt_return_seeking = MeanVarianceOptimizer(
            session=db_session,
            config=MeanVarianceConfig(dry_run=True, risk_aversion=0.001),
        )
        # Very high risk aversion → λ=1/1000=0.001 → near min-variance → concentrates in LR
        opt_conservative = MeanVarianceOptimizer(
            session=db_session,
            config=MeanVarianceConfig(dry_run=True, risk_aversion=1000.0),
        )

        r_return_seeking = opt_return_seeking.optimize(
            assets=assets, expected_returns=mu, objective=OptimizationObjective.MAXIMUM_UTILITY
        )
        r_conservative = opt_conservative.optimize(
            assets=assets, expected_returns=mu, objective=OptimizationObjective.MAXIMUM_UTILITY
        )

        # Return-seeking (low RA) > conservative (high RA) in expected return
        assert r_return_seeking.expected_return is not None
        assert r_conservative.expected_return is not None
        assert r_return_seeking.expected_return > r_conservative.expected_return

    def test_max_utility_tilts_toward_high_return(self, db_session: Session) -> None:
        assets = ["D0", "D1"]
        vols = [0.02, 0.02]  # equal vol
        _seed_covariance(db_session, assets, _diagonal_cov(vols))
        mu = {"D0": 0.05, "D1": 0.15}

        result = _build_optimizer(db_session, risk_aversion=0.1).optimize(
            assets=assets,
            expected_returns=mu,
            objective=OptimizationObjective.MAXIMUM_UTILITY,
        )
        # With equal vol and very low risk aversion, D1 (higher return) gets more weight
        assert result.target_weights["D1"] > result.target_weights["D0"]


# ---------------------------------------------------------------------------
# Target return objective
# ---------------------------------------------------------------------------


class TestTargetReturn:
    def test_target_return_approximately_achieved(self, db_session: Session) -> None:
        assets = ["TR0", "TR1", "TR2"]
        vols = [0.02, 0.03, 0.04]
        mu = {"TR0": 0.05, "TR1": 0.10, "TR2": 0.15}
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        target = 0.09
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=mu,
            objective=OptimizationObjective.TARGET_RETURN,
            target_return=target,
        )
        assert result.solver_status in (SolverStatus.OPTIMAL, SolverStatus.SUBOPTIMAL)
        assert result.expected_return is not None
        assert math.isclose(result.expected_return, target, rel_tol=0.05)


# ---------------------------------------------------------------------------
# Target volatility objective
# ---------------------------------------------------------------------------


class TestTargetVolatility:
    def test_target_volatility_approximately_achieved(self, db_session: Session) -> None:
        assets = ["TV0", "TV1", "TV2"]
        vols = [0.01, 0.04, 0.08]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))
        mu = {"TV0": 0.05, "TV1": 0.10, "TV2": 0.20}

        target_vol = 0.04
        result = _build_optimizer(db_session, risk_aversion=1.0).optimize(
            assets=assets,
            expected_returns=mu,
            objective=OptimizationObjective.TARGET_VOLATILITY,
            target_volatility=target_vol,
        )
        assert result.solver_status in (SolverStatus.OPTIMAL, SolverStatus.SUBOPTIMAL)
        if result.expected_volatility is not None:
            assert math.isclose(result.expected_volatility, target_vol, rel_tol=0.15)


# ---------------------------------------------------------------------------
# Constraint enforcement
# ---------------------------------------------------------------------------


class TestConstraints:
    def test_max_weight_per_asset_respected(self, db_session: Session) -> None:
        assets = ["MW0", "MW1", "MW2"]
        vols = [0.01, 0.05, 0.10]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        constraints = OptimizerConstraints(per_asset_max={"MW0": 0.40, "MW1": 0.40, "MW2": 0.40})
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        for asset, w in result.target_weights.items():
            assert w <= 0.40 + 1e-5, f"{asset} weight {w:.6f} exceeds cap"

    def test_global_max_weight_respected(self, db_session: Session) -> None:
        assets = ["GM0", "GM1", "GM2", "GM3"]
        vols = [0.01, 0.02, 0.03, 0.04]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        constraints = OptimizerConstraints(global_max_weight=0.35)
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(4),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        for w in result.target_weights.values():
            assert w <= 0.35 + 1e-5

    def test_min_weight_floor_respected(self, db_session: Session) -> None:
        assets = ["FL0", "FL1", "FL2"]
        vols = [0.01, 0.02, 0.03]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        constraints = OptimizerConstraints(global_min_weight=0.10)
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        for w in result.target_weights.values():
            assert w >= 0.10 - 1e-5

    def test_sector_cap_respected(self, db_session: Session) -> None:
        assets = ["SC0", "SC1", "SC2", "SC3"]
        vols = [0.01, 0.01, 0.05, 0.05]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        constraints = OptimizerConstraints(
            sector_caps={"tech": 0.40},
            sector_map={"SC0": "tech", "SC1": "tech"},
        )
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(4),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        sector_total = result.target_weights["SC0"] + result.target_weights["SC1"]
        assert sector_total <= 0.40 + 1e-5

    def test_turnover_penalty_reduces_deviation_from_current(self, db_session: Session) -> None:
        assets = ["TP0", "TP1", "TP2"]
        vols = [0.01, 0.05, 0.10]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))
        current = {"TP0": 0.5, "TP1": 0.3, "TP2": 0.2}

        no_penalty = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            current_weights=current,
            constraints=OptimizerConstraints(turnover_penalty=0.0),
        )
        high_penalty = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            current_weights=current,
            constraints=OptimizerConstraints(turnover_penalty=10.0),
        )
        # High turnover penalty → target weights stay closer to current weights
        assert high_penalty.realized_turnover is not None
        assert no_penalty.realized_turnover is not None
        assert high_penalty.realized_turnover <= no_penalty.realized_turnover + 1e-3

    def test_infeasible_lb_returns_fallback(self, db_session: Session) -> None:
        assets = ["IF0", "IF1"]
        vols = [0.02, 0.03]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        constraints = OptimizerConstraints(per_asset_min={"IF0": 0.7, "IF1": 0.7})
        result = _build_optimizer(db_session, fallback_mode=FallbackMode.EQUAL_WEIGHTS).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED
        assert result.infeasibility_reason is not None

    def test_infeasible_ub_returns_fallback(self, db_session: Session) -> None:
        assets = ["IU0", "IU1"]
        vols = [0.02, 0.03]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        # Both assets capped at 0.3, but sum(ub) = 0.6 < 1.0
        constraints = OptimizerConstraints(per_asset_max={"IU0": 0.3, "IU1": 0.3})
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            constraints=constraints,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_nan_in_expected_returns_falls_back(self, db_session: Session) -> None:
        assets = ["NAN0", "NAN1"]
        vols = [0.02, 0.03]
        _seed_covariance(db_session, assets, _diagonal_cov(vols))

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns={"NAN0": float("nan"), "NAN1": 0.1},
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED

    def test_dimension_mismatch_falls_back(self, db_session: Session) -> None:
        # Seed cov for 2 assets, but request 3
        assets_2 = ["DM0", "DM1"]
        assets_3 = ["DM0", "DM1", "DM2"]
        vols = [0.02, 0.03]
        _seed_covariance(db_session, assets_2, _diagonal_cov(vols))

        result = _build_optimizer(db_session).optimize(
            assets=assets_3,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED

    def test_missing_covariance_falls_back(self, db_session: Session) -> None:
        result = _build_optimizer(db_session).optimize(
            assets=["MC0", "MC1"],
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED
        assert "covariance" in (result.infeasibility_reason or "").lower()

    def test_empty_assets_returns_failed(self, db_session: Session) -> None:
        result = _build_optimizer(db_session).optimize(
            assets=[], expected_returns={}, objective=OptimizationObjective.MINIMUM_VARIANCE
        )
        assert result.solver_status == SolverStatus.FAILED

    def test_not_positive_definite_covariance_falls_back(self, db_session: Session) -> None:
        assets = ["PD0", "PD1"]
        vols = [0.02, 0.02]
        _seed_covariance(db_session, assets, _diagonal_cov(vols), is_positive_definite=False)

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED

    def test_provided_cov_with_nan_falls_back(self, db_session: Session) -> None:
        assets = ["PC0", "PC1"]
        bad_cov = {"PC0": {"PC0": float("nan"), "PC1": 0.0}, "PC1": {"PC0": 0.0, "PC1": 0.0004}}
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            covariance_matrix=bad_cov,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED

    def test_provided_asymmetric_cov_falls_back(self, db_session: Session) -> None:
        assets = ["AS0", "AS1"]
        # Non-symmetric covariance
        asym_cov = {"AS0": {"AS0": 0.0004, "AS1": 0.0001}, "AS1": {"AS0": 0.0009, "AS1": 0.0009}}
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            covariance_matrix=asym_cov,
        )
        assert result.solver_status == SolverStatus.FALLBACK_USED


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_equal_weights_on_missing_covariance(self, db_session: Session) -> None:
        assets = ["FEW0", "FEW1", "FEW2"]
        result = _build_optimizer(db_session, fallback_mode=FallbackMode.EQUAL_WEIGHTS).optimize(
            assets=assets,
            expected_returns=_equal_returns(3),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.fallback_mode == FallbackMode.EQUAL_WEIGHTS
        for w in result.target_weights.values():
            assert math.isclose(w, 1.0 / 3, rel_tol=1e-5)

    def test_fallback_previous_weights_preserves_current(self, db_session: Session) -> None:
        assets = ["FPW0", "FPW1"]
        current = {"FPW0": 0.7, "FPW1": 0.3}
        result = MeanVarianceOptimizer(
            session=db_session,
            config=MeanVarianceConfig(dry_run=True, fallback_mode=FallbackMode.PREVIOUS_WEIGHTS),
        ).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            current_weights=current,
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.fallback_mode == FallbackMode.PREVIOUS_WEIGHTS
        assert math.isclose(result.target_weights["FPW0"], 0.7, rel_tol=1e-5)
        assert math.isclose(result.target_weights["FPW1"], 0.3, rel_tol=1e-5)


# ---------------------------------------------------------------------------
# Covariance integration
# ---------------------------------------------------------------------------


class TestCovarianceIntegration:
    def test_covariance_snapshot_id_recorded(self, db_session: Session) -> None:
        assets = ["CSI0", "CSI1"]
        snap_id = _seed_covariance(
            db_session, assets, _diagonal_cov([0.02, 0.03]), snapshot_id="test-cov-snap"
        )
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.covariance_snapshot_id == snap_id

    def test_provided_covariance_overrides_snapshot(self, db_session: Session) -> None:
        assets = ["PCO0", "PCO1"]
        cov_dict = {a: {b: 0.0025 if a == b else 0.0 for b in assets} for a in assets}

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            covariance_matrix=cov_dict,
            covariance_snapshot_id="provided-snap",
        )
        assert result.solver_status in (SolverStatus.OPTIMAL, SolverStatus.SUBOPTIMAL)
        assert result.covariance_snapshot_id == "provided-snap"

    def test_expected_return_source_recorded(self, db_session: Session) -> None:
        assets = ["ERS0", "ERS1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            expected_return_source="blended_quality_score",
        )
        assert result.expected_return_source == "blended_quality_score"


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_persist(self, db_session: Session) -> None:
        assets = ["DR0", "DR1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))

        run_id = str(uuid4())
        _build_optimizer(db_session, dry_run=True).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            run_id=run_id,
        )

        repo = OptimizerRunRepository(db_session)
        row = repo.get_by_run_id(run_id)
        assert row is None

    def test_non_dry_run_persists(self, db_session: Session) -> None:
        assets = ["NDR0", "NDR1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))

        run_id = str(uuid4())
        _build_optimizer(db_session, dry_run=False).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            run_id=run_id,
        )

        repo = OptimizerRunRepository(db_session)
        row = repo.get_by_run_id(run_id)
        assert row is not None
        assert row.run_id == run_id
        assert row.dry_run is False

    def test_fallback_also_persisted_in_non_dry_run(self, db_session: Session) -> None:
        run_id = str(uuid4())
        _build_optimizer(db_session, dry_run=False).optimize(
            assets=["FB0", "FB1"],
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            run_id=run_id,
        )
        row = OptimizerRunRepository(db_session).get_by_run_id(run_id)
        assert row is not None
        assert row.solver_status == "fallback_used"


# ---------------------------------------------------------------------------
# Audit artifacts
# ---------------------------------------------------------------------------


class TestAuditArtifacts:
    def test_optimizer_run_contains_required_metadata(self, db_session: Session) -> None:
        assets = ["AM0", "AM1", "AM2"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03, 0.04]))
        run_id = str(uuid4())

        _build_optimizer(db_session, dry_run=False).optimize(
            assets=assets,
            expected_returns={"AM0": 0.05, "AM1": 0.10, "AM2": 0.15},
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            expected_return_source="research_quality_score",
            run_id=run_id,
        )

        row = OptimizerRunRepository(db_session).get_by_run_id(run_id)
        assert row is not None
        assert row.objective == "minimum_variance"
        assert row.expected_return_source == "research_quality_score"
        assert row.covariance_snapshot_id is not None
        assert len(row.asset_universe) == 3
        assert isinstance(row.target_weights, dict)
        assert isinstance(row.constraints_applied, list)
        assert row.convergence_achieved is True

    def test_run_id_present_in_result(self, db_session: Session) -> None:
        assets = ["RID0", "RID1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        run_id = str(uuid4())

        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            run_id=run_id,
        )
        assert result.run_id == run_id

    def test_inputs_hash_in_metadata(self, db_session: Session) -> None:
        assets = ["IH0", "IH1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert "inputs_hash" in result.metadata


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_produce_same_weights(self, db_session: Session) -> None:
        assets = ["DET0", "DET1", "DET2"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.01, 0.03, 0.05]))
        mu = {"DET0": 0.05, "DET1": 0.10, "DET2": 0.15}

        r1 = _build_optimizer(db_session).optimize(
            assets=assets, expected_returns=mu, objective=OptimizationObjective.MINIMUM_VARIANCE
        )
        r2 = _build_optimizer(db_session).optimize(
            assets=assets, expected_returns=mu, objective=OptimizationObjective.MINIMUM_VARIANCE
        )
        for a in assets:
            assert math.isclose(r1.target_weights[a], r2.target_weights[a], rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    def test_result_to_jsonable(self, db_session: Session) -> None:
        import json

        assets = ["J0", "J1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        payload = MeanVarianceOptimizer.result_to_jsonable(result)
        serialized = json.dumps(payload)
        assert len(serialized) > 0
        parsed = json.loads(serialized)
        assert "target_weights" in parsed

    def test_all_fields_present_on_success(self, db_session: Session) -> None:
        assets = ["FP0", "FP1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        result = _build_optimizer(db_session).optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        assert result.run_id is not None
        assert result.generated_at is not None
        assert result.duration_seconds > 0
        assert isinstance(result.constraints_applied, list)
        assert isinstance(result.binding_constraints, list)
        assert isinstance(result.warnings, list)
        assert result.expected_return is not None
        assert result.expected_volatility is not None
        assert result.realized_turnover is not None

    def test_get_latest_run(self, db_session: Session) -> None:
        assets = ["GL0", "GL1"]
        _seed_covariance(db_session, assets, _diagonal_cov([0.02, 0.03]))
        opt = _build_optimizer(db_session, dry_run=False)
        opt.optimize(
            assets=assets,
            expected_returns=_equal_returns(2),
            objective=OptimizationObjective.MINIMUM_VARIANCE,
        )
        latest = opt.get_latest_run(dry_run=False)
        assert latest is not None
        assert latest.dry_run is False
