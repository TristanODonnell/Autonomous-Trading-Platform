from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.optimizer import (
    FallbackMode,
    OptimizationObjective,
    OptimizationResult,
    OptimizerConstraints,
    SolverStatus,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_mvo_duration_seconds,
    ratp_mvo_expected_return,
    ratp_mvo_expected_volatility,
    ratp_mvo_infeasible_total,
    ratp_mvo_run_total,
    ratp_mvo_turnover,
)
from autonomous_trading_platform.storage.sor.models.optimizer_runs import OptimizerRunRow
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.optimizer_run_repository import (
    OptimizerRunRepository,
)

logger = get_logger(__name__)

# Structured log event names
MVO_STARTED = "MEAN_VARIANCE_OPTIMIZATION_STARTED"
MVO_COMPLETED = "MEAN_VARIANCE_OPTIMIZATION_COMPLETED"
MVO_INFEASIBLE = "MEAN_VARIANCE_OPTIMIZATION_INFEASIBLE"
MVO_FALLBACK = "MEAN_VARIANCE_FALLBACK_USED"

_PGD_MAX_ITER = 3000
_PGD_TOL = 1e-9
_LAMBDA_SEARCH_ITER = 60
_MAX_CONDITION_NUMBER = 1e12
_MIN_FEASIBLE_SUM_GAP = 1e-6


class InfeasibleConstraintsError(ValueError):
    """Raised when no feasible portfolio exists under the given constraints."""


@dataclass(frozen=True)
class MeanVarianceConfig:
    """Runtime parameters for the MVO solver."""

    risk_aversion: float = 1.0
    covariance_lookback_window: int = 60
    covariance_snapshot_type: str = "symbol"
    regularization: float = 1e-8
    pgd_max_iter: int = _PGD_MAX_ITER
    pgd_tol: float = _PGD_TOL
    fallback_mode: FallbackMode = FallbackMode.EQUAL_WEIGHTS
    dry_run: bool = True


@dataclass
class _QpProblem:
    """Input to the pure-numpy solver."""

    cov: np.ndarray
    mu: np.ndarray
    lb: np.ndarray
    ub: np.ndarray
    risk_aversion: float
    sector_indices: dict[str, list[int]]
    sector_caps: dict[str, float]
    current_weights: np.ndarray | None
    turnover_penalty: float
    regularization: float


@dataclass(frozen=True)
class _QpSolution:
    weights: np.ndarray
    iterations: int
    converged: bool
    warnings: list[str]


class _NumpyQPSolver:
    """Pure-numpy projected gradient descent for box-simplex constrained QPs.

    Solves: min (1/2) w^T Σ w - λ μ^T w
    s.t.    sum(w) = 1
            lb <= w <= ub
            sum_{i in sector} w_i <= sector_cap  (for each sector)

    Turnover handled via L1 penalty added to the gradient (subgradient descent).

    Designed to be replaced by a CVXPY backend (TASK-5.6) without changing
    the MeanVarianceOptimizer interface.
    """

    def solve(self, problem: _QpProblem) -> _QpSolution:
        cov = problem.cov + problem.regularization * np.eye(len(problem.mu))
        mu = problem.mu
        lb = problem.lb
        ub = problem.ub
        lam = problem.risk_aversion
        n = len(mu)
        warnings: list[str] = []

        # Lipschitz constant of gradient
        try:
            L = float(np.linalg.eigvalsh(cov).max()) + 1e-10
        except np.linalg.LinAlgError:
            L = float(np.max(np.abs(cov))) * n + 1e-10
        step = 1.0 / L

        # Good initial guess: inverse-vol
        diag_var = np.maximum(np.diag(cov), 1e-20)
        w = 1.0 / np.sqrt(diag_var)
        w = np.clip(w, lb, ub)
        w = self._project_box_simplex(w, lb, ub)

        tp = problem.turnover_penalty
        w0 = problem.current_weights
        max_iter = problem.pgd_max_iter if hasattr(problem, "pgd_max_iter") else _PGD_MAX_ITER
        tol = problem.pgd_tol if hasattr(problem, "pgd_tol") else _PGD_TOL

        converged = False
        last_iter = 0
        for _it in range(max_iter):
            last_iter = _it
            # Smooth gradient of the quadratic term only
            grad = cov @ w - lam * mu
            v = w - step * grad

            # Proximal step for L1 turnover penalty (soft-thresholding around w0)
            if tp > 0 and w0 is not None:
                threshold = tp * step
                delta = v - w0
                delta_soft = np.sign(delta) * np.maximum(np.abs(delta) - threshold, 0.0)
                v = w0 + delta_soft

            w_new = self._project_box_simplex(v, lb, ub)
            w_new = self._enforce_sector_caps(
                w_new, problem.sector_indices, problem.sector_caps, lb
            )

            delta = float(np.max(np.abs(w_new - w)))
            w = w_new
            if delta < tol:
                converged = True
                break

        if not converged:
            warnings.append(f"PGD did not converge within {last_iter + 1} iterations")

        return _QpSolution(
            weights=w, iterations=last_iter + 1, converged=converged, warnings=warnings
        )

    @staticmethod
    def _project_box_simplex(v: np.ndarray, lb: np.ndarray, ub: np.ndarray) -> np.ndarray:
        """Project v onto {w: sum(w)=1, lb<=w<=ub} via KKT bisection.

        f(θ) = Σ clip(v_i - θ, lb_i, ub_i) - 1 is strictly decreasing.
        Find θ* such that f(θ*) = 0 by bisection.
        """
        lb_sum = float(np.sum(lb))
        ub_sum = float(np.sum(ub))
        if lb_sum > 1.0 + _MIN_FEASIBLE_SUM_GAP:
            raise InfeasibleConstraintsError(f"Lower bounds sum {lb_sum:.6f} exceeds 1.0")
        if ub_sum < 1.0 - _MIN_FEASIBLE_SUM_GAP:
            raise InfeasibleConstraintsError(f"Upper bounds sum {ub_sum:.6f} is less than 1.0")

        def f(theta: float) -> float:
            return float(np.sum(np.clip(v - theta, lb, ub))) - 1.0

        theta_lo = float(np.min(v - ub)) - 1.0
        theta_hi = float(np.max(v - lb)) + 1.0

        # Ensure bracket (expand if needed)
        for _ in range(60):
            if f(theta_lo) >= -1e-12 and f(theta_hi) <= 1e-12:
                break
            if f(theta_lo) < 0:
                theta_lo -= 2.0
            if f(theta_hi) > 0:
                theta_hi += 2.0

        # Bisection
        for _ in range(100):
            theta_mid = (theta_lo + theta_hi) * 0.5
            val = f(theta_mid)
            if abs(val) < 1e-12:
                break
            if val > 0:
                theta_lo = theta_mid
            else:
                theta_hi = theta_mid

        return np.clip(v - (theta_lo + theta_hi) * 0.5, lb, ub)

    @staticmethod
    def _enforce_sector_caps(
        w: np.ndarray,
        sector_indices: dict[str, list[int]],
        sector_caps: dict[str, float],
        lb: np.ndarray,
    ) -> np.ndarray:
        """Proportionally scale down over-weight sectors, respecting lower bounds."""
        for sector, cap in sector_caps.items():
            indices = sector_indices.get(sector, [])
            if not indices:
                continue
            total = float(np.sum(w[indices]))
            if total > cap + 1e-8:
                excess = total - cap
                room = np.array([max(w[i] - lb[i], 0.0) for i in indices])
                total_room = room.sum()
                if total_room > 1e-10:
                    for _idx, i in enumerate(indices):
                        w[i] = lb[i] + (w[i] - lb[i]) * max(0.0, 1.0 - excess / total_room)
        return w


class MeanVarianceOptimizer:
    """Constrained mean-variance portfolio optimizer.

    Produces target allocation weights that minimize portfolio variance for a
    given expected return, maximise risk-adjusted utility, or hit a target
    volatility/return — subject to allocation caps, sector limits, and
    optional turnover constraints.

    Backward compatible: dry_run=True (the default) means the optimizer
    computes and persists results for inspection but does NOT write
    AllocationOverrides.  Set dry_run=False only when the optimizer is
    explicitly wired into the runtime allocation path.

    The underlying QP solver (_NumpyQPSolver) is isolated behind a simple
    interface so it can be replaced by a CVXPY backend (TASK-5.6) without
    touching this class.
    """

    def __init__(
        self,
        *,
        session: Session,
        config: MeanVarianceConfig | None = None,
        optimizer_run_repo: OptimizerRunRepository | None = None,
        correlation_repo: CorrelationSnapshotRepository | None = None,
        solver: _NumpyQPSolver | None = None,
    ) -> None:
        self._session = session
        self._config = config or MeanVarianceConfig()
        self._run_repo = optimizer_run_repo or OptimizerRunRepository(session)
        self._corr_repo = correlation_repo or CorrelationSnapshotRepository(session)
        self._solver = solver or _NumpyQPSolver()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def optimize(
        self,
        *,
        assets: list[str],
        expected_returns: dict[str, float],
        current_weights: dict[str, float] | None = None,
        objective: OptimizationObjective = OptimizationObjective.MINIMUM_VARIANCE,
        constraints: OptimizerConstraints | None = None,
        expected_return_source: str = "caller_supplied",
        run_id: str | None = None,
        covariance_matrix: dict[str, dict[str, float]] | None = None,
        covariance_snapshot_id: str | None = None,
        target_return: float | None = None,
        target_volatility: float | None = None,
    ) -> OptimizationResult:
        """Compute constrained MVO target weights.

        Args:
            assets: Ordered list of asset/strategy identifiers.
            expected_returns: Per-asset expected returns (e.g., annualized).
            current_weights: Current portfolio weights for turnover computation.
            objective: Optimization objective.
            constraints: Allocation and risk constraints.
            expected_return_source: Human-readable label for return source.
            run_id: Caller-supplied ID for lineage tracing.
            covariance_matrix: Optional pre-loaded covariance dict-of-dict.
                               If None, loaded from TASK-5.1 snapshots.
            covariance_snapshot_id: ID of the covariance snapshot (for audit).
            target_return: Required for TARGET_RETURN objective.
            target_volatility: Required for TARGET_VOLATILITY objective.
        """
        if run_id is None:
            run_id = str(uuid4())

        wall_start = perf_counter()
        now = datetime.now(UTC)
        constraints = constraints or OptimizerConstraints()
        current_weights = current_weights or {a: 1.0 / max(len(assets), 1) for a in assets}

        logger.info(
            "%s run_id=%s objective=%s assets=%d dry_run=%s",
            MVO_STARTED,
            run_id,
            objective,
            len(assets),
            self._config.dry_run,
        )

        ratp_mvo_run_total.add(1, {"objective": str(objective)})

        if len(assets) == 0:
            return self._empty_result(run_id=run_id, now=now, duration=perf_counter() - wall_start)

        # Load covariance matrix
        cov_matrix_np, loaded_snapshot_id, cov_warnings = self._load_covariance(
            assets=assets,
            provided_matrix=covariance_matrix,
            provided_snapshot_id=covariance_snapshot_id,
        )
        effective_snapshot_id = loaded_snapshot_id or covariance_snapshot_id

        if cov_matrix_np is None:
            return self._fallback_result(
                run_id=run_id,
                now=now,
                assets=assets,
                current_weights=current_weights,
                reason="No valid covariance matrix available",
                infeasibility_reason="covariance_unavailable",
                snapshot_id=effective_snapshot_id,
                expected_return_source=expected_return_source,
                warnings=cov_warnings,
                duration=perf_counter() - wall_start,
            )

        # Validate inputs
        mu = np.array([expected_returns.get(a, 0.0) for a in assets], dtype=float)
        w0 = np.array([current_weights.get(a, 1.0 / len(assets)) for a in assets], dtype=float)

        validation_warnings = list(cov_warnings)
        val_error = self._validate_inputs(cov_matrix_np, mu, assets)
        if val_error:
            return self._fallback_result(
                run_id=run_id,
                now=now,
                assets=assets,
                current_weights=current_weights,
                reason=val_error,
                infeasibility_reason=val_error,
                snapshot_id=effective_snapshot_id,
                expected_return_source=expected_return_source,
                warnings=validation_warnings,
                duration=perf_counter() - wall_start,
            )

        # Build constraint bounds
        lb, ub, bounds_error = self._build_bounds(assets, constraints)
        if bounds_error:
            return self._fallback_result(
                run_id=run_id,
                now=now,
                assets=assets,
                current_weights=current_weights,
                reason=bounds_error,
                infeasibility_reason=bounds_error,
                snapshot_id=effective_snapshot_id,
                expected_return_source=expected_return_source,
                warnings=validation_warnings,
                duration=perf_counter() - wall_start,
            )

        # Build sector index map
        sector_indices = self._build_sector_indices(assets, constraints)
        sector_caps = dict(constraints.sector_caps)

        # Determine lambda and solve
        try:
            target_weights, iterations, converged, solve_warnings, risk_aversion_used = (
                self._solve_by_objective(
                    objective=objective,
                    cov=cov_matrix_np,
                    mu=mu,
                    lb=lb,
                    ub=ub,
                    sector_indices=sector_indices,
                    sector_caps=sector_caps,
                    w0=w0,
                    constraints=constraints,
                    target_return=target_return,
                    target_volatility=target_volatility,
                )
            )
        except InfeasibleConstraintsError as exc:
            logger.warning("%s run_id=%s reason=%s", MVO_INFEASIBLE, run_id, str(exc))
            ratp_mvo_infeasible_total.add(1)
            return self._fallback_result(
                run_id=run_id,
                now=now,
                assets=assets,
                current_weights=current_weights,
                reason=str(exc),
                infeasibility_reason=str(exc),
                snapshot_id=effective_snapshot_id,
                expected_return_source=expected_return_source,
                warnings=validation_warnings,
                duration=perf_counter() - wall_start,
            )

        validation_warnings.extend(solve_warnings)

        # Compute portfolio statistics
        port_return = float(mu @ target_weights)
        port_variance = float(target_weights @ cov_matrix_np @ target_weights)
        port_vol = math.sqrt(max(port_variance, 0.0))
        obj_val = 0.5 * port_variance - risk_aversion_used * port_return
        turnover = float(np.sum(np.abs(target_weights - w0)))

        # Diagnose binding constraints
        binding = self._find_binding_constraints(
            weights=target_weights,
            lb=lb,
            ub=ub,
            sector_indices=sector_indices,
            sector_caps=sector_caps,
            constraints_applied=[],
        )
        constraints_applied = self._list_constraints(constraints)

        # Turnover warning
        if constraints.max_turnover is not None and turnover > constraints.max_turnover + 1e-4:
            validation_warnings.append(
                f"Realized turnover {turnover:.4f} exceeds max_turnover "
                f"{constraints.max_turnover:.4f}"
            )

        weights_dict = {a: float(target_weights[i]) for i, a in enumerate(assets)}
        duration = perf_counter() - wall_start
        solver_status = SolverStatus.OPTIMAL if converged else SolverStatus.SUBOPTIMAL

        result = OptimizationResult(
            run_id=run_id,
            objective=objective,
            solver_status=solver_status,
            assets=assets,
            target_weights=weights_dict,
            current_weights={a: float(w0[i]) for i, a in enumerate(assets)},
            expected_return=round(port_return, 8),
            expected_volatility=round(port_vol, 8),
            objective_value=round(obj_val, 8),
            realized_turnover=round(turnover, 6),
            constraints_applied=constraints_applied,
            binding_constraints=binding,
            infeasibility_reason=None,
            fallback_mode=FallbackMode.NONE,
            covariance_snapshot_id=effective_snapshot_id,
            expected_return_source=expected_return_source,
            risk_aversion=risk_aversion_used,
            iterations_to_convergence=iterations,
            convergence_achieved=converged,
            generated_at=now,
            duration_seconds=round(duration, 4),
            dry_run=self._config.dry_run,
            warnings=validation_warnings,
            metadata={"inputs_hash": self._hash_inputs(assets, mu, cov_matrix_np)},
        )

        if not self._config.dry_run:
            self._persist(result)

        self._emit_events(result)
        self._record_metrics(result)

        return result

    # ------------------------------------------------------------------
    # Objectives dispatch
    # ------------------------------------------------------------------

    def _solve_by_objective(
        self,
        *,
        objective: OptimizationObjective,
        cov: np.ndarray,
        mu: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        sector_indices: dict[str, list[int]],
        sector_caps: dict[str, float],
        w0: np.ndarray,
        constraints: OptimizerConstraints,
        target_return: float | None,
        target_volatility: float | None,
    ) -> tuple[np.ndarray, int, bool, list[str], float]:
        """Returns (weights, iterations, converged, warnings, effective_lambda)."""
        tp = constraints.turnover_penalty

        if objective == OptimizationObjective.MINIMUM_VARIANCE:
            sol = self._solve_qp(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                lam=0.0,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=tp,
            )
            return sol.weights, sol.iterations, sol.converged, sol.warnings, 0.0

        elif objective == OptimizationObjective.MAXIMUM_UTILITY:
            # Convention: higher risk_aversion = more conservative (less return-seeking).
            # λ = 1 / risk_aversion so that large risk_aversion → λ ≈ 0 → min-variance.
            lam = 1.0 / max(self._config.risk_aversion, 1e-6)
            sol = self._solve_qp(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                lam=lam,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=tp,
            )
            return sol.weights, sol.iterations, sol.converged, sol.warnings, lam

        elif objective == OptimizationObjective.TARGET_RETURN:
            if target_return is None:
                target_return = float(np.mean(mu))
            w, iters, conv, warns, lam = self._binary_search_lambda(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=tp,
                target_fn=lambda weights: float(mu @ weights) - target_return,
                lam_lo=0.0,
                lam_hi=1000.0,
            )
            return w, iters, conv, warns, lam

        elif objective == OptimizationObjective.TARGET_VOLATILITY:
            if target_volatility is None:
                target_volatility = float(np.sqrt(np.mean(np.diag(cov))))

            def vol_fn(weights: np.ndarray) -> float:
                return (
                    float(math.sqrt(max(float(weights @ cov @ weights), 0.0))) - target_volatility
                )

            w, iters, conv, warns, lam = self._binary_search_lambda(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=tp,
                target_fn=vol_fn,
                lam_lo=0.0,
                lam_hi=1000.0,
            )
            return w, iters, conv, warns, lam

        else:
            sol = self._solve_qp(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                lam=0.0,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=tp,
            )
            return sol.weights, sol.iterations, sol.converged, sol.warnings, 0.0

    def _solve_qp(
        self,
        *,
        cov: np.ndarray,
        mu: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        lam: float,
        sector_indices: dict[str, list[int]],
        sector_caps: dict[str, float],
        w0: np.ndarray | None,
        turnover_penalty: float,
    ) -> _QpSolution:
        problem = _QpProblem(
            cov=cov,
            mu=mu,
            lb=lb,
            ub=ub,
            risk_aversion=lam,
            sector_indices=sector_indices,
            sector_caps=sector_caps,
            current_weights=w0,
            turnover_penalty=turnover_penalty,
            regularization=self._config.regularization,
        )
        # Attach iteration limits from config
        problem.pgd_max_iter = self._config.pgd_max_iter  # type: ignore[attr-defined]
        problem.pgd_tol = self._config.pgd_tol  # type: ignore[attr-defined]
        return self._solver.solve(problem)

    def _binary_search_lambda(
        self,
        *,
        cov: np.ndarray,
        mu: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        sector_indices: dict[str, list[int]],
        sector_caps: dict[str, float],
        w0: np.ndarray,
        turnover_penalty: float,
        target_fn: Any,
        lam_lo: float,
        lam_hi: float,
    ) -> tuple[np.ndarray, int, bool, list[str], float]:
        """Binary search on λ to hit a scalar target (return or volatility)."""
        total_iters = 0
        all_warnings: list[str] = []
        best_w = None
        best_lam = lam_lo

        for _ in range(_LAMBDA_SEARCH_ITER):
            lam_mid = (lam_lo + lam_hi) * 0.5
            sol = self._solve_qp(
                cov=cov,
                mu=mu,
                lb=lb,
                ub=ub,
                lam=lam_mid,
                sector_indices=sector_indices,
                sector_caps=sector_caps,
                w0=w0,
                turnover_penalty=turnover_penalty,
            )
            total_iters += sol.iterations
            all_warnings.extend(sol.warnings)
            best_w = sol.weights
            best_lam = lam_mid
            best_conv = sol.converged

            val = target_fn(sol.weights)
            if abs(val) < 1e-6:
                break
            # target_fn < 0 means we need more return/vol → increase λ
            if val < 0:
                lam_lo = lam_mid
            else:
                lam_hi = lam_mid

        return best_w, total_iters, best_conv, all_warnings, best_lam  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Covariance loading
    # ------------------------------------------------------------------

    def _load_covariance(
        self,
        *,
        assets: list[str],
        provided_matrix: dict[str, dict[str, float]] | None,
        provided_snapshot_id: str | None,
    ) -> tuple[np.ndarray | None, str | None, list[str]]:
        warnings: list[str] = []

        if provided_matrix is not None:
            try:
                mat = np.array(
                    [[provided_matrix[a][b] for b in assets] for a in assets], dtype=float
                )
                return mat, provided_snapshot_id, warnings
            except (KeyError, TypeError) as exc:
                warnings.append(f"Provided covariance matrix invalid: {exc}")
                return None, provided_snapshot_id, warnings

        snap = self._corr_repo.get_latest_covariance(
            snapshot_type=self._config.covariance_snapshot_type,
            lookback_window=self._config.covariance_lookback_window,
        )
        if snap is None:
            warnings.append("No covariance snapshot found in storage")
            return None, None, warnings

        if not snap.is_positive_definite:
            warnings.append(f"Covariance snapshot {snap.snapshot_id} is not positive-definite")
            return None, snap.snapshot_id, warnings

        if snap.condition_number is not None and snap.condition_number > _MAX_CONDITION_NUMBER:
            warnings.append(
                f"Covariance snapshot condition number {snap.condition_number:.2e} exceeds threshold"
            )
            return None, snap.snapshot_id, warnings

        available = set(snap.asset_universe)
        missing = set(assets) - available
        if missing:
            warnings.append(f"Assets not in covariance snapshot: {sorted(missing)}")
            return None, snap.snapshot_id, warnings

        try:
            cov_dict = snap.covariance_matrix
            mat = np.array([[cov_dict[a][b] for b in assets] for a in assets], dtype=float)
        except (KeyError, TypeError) as exc:
            warnings.append(f"Failed to extract covariance sub-matrix: {exc}")
            return None, snap.snapshot_id, warnings

        return mat, snap.snapshot_id, warnings

    # ------------------------------------------------------------------
    # Constraint helpers
    # ------------------------------------------------------------------

    def _build_bounds(
        self, assets: list[str], constraints: OptimizerConstraints
    ) -> tuple[np.ndarray, np.ndarray, str | None]:
        global_lb = 0.0 if constraints.long_only else -1.0
        global_lb = max(global_lb, constraints.global_min_weight)
        global_ub = constraints.global_max_weight
        effective_ub = global_ub * (1.0 - constraints.cash_reserve)

        lb = np.array([constraints.per_asset_min.get(a, global_lb) for a in assets], dtype=float)
        ub = np.array([constraints.per_asset_max.get(a, effective_ub) for a in assets], dtype=float)

        if np.any(lb > ub):
            return lb, ub, "Per-asset lower bound exceeds upper bound for some assets"
        if float(lb.sum()) > 1.0 + _MIN_FEASIBLE_SUM_GAP:
            return lb, ub, f"Lower bounds sum {lb.sum():.6f} exceeds 1.0"
        if float(ub.sum()) < 1.0 - _MIN_FEASIBLE_SUM_GAP:
            return lb, ub, f"Upper bounds sum {ub.sum():.6f} is less than 1.0"

        return lb, ub, None

    @staticmethod
    def _build_sector_indices(
        assets: list[str], constraints: OptimizerConstraints
    ) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for i, asset in enumerate(assets):
            sector = constraints.sector_map.get(asset)
            if sector and sector in constraints.sector_caps:
                result.setdefault(sector, []).append(i)
        return result

    @staticmethod
    def _list_constraints(constraints: OptimizerConstraints) -> list[str]:
        applied = ["sum_to_one"]
        if constraints.long_only:
            applied.append("long_only")
        if constraints.global_max_weight < 1.0:
            applied.append(f"max_weight={constraints.global_max_weight:.2f}")
        if constraints.global_min_weight > 0:
            applied.append(f"min_weight={constraints.global_min_weight:.4f}")
        if constraints.per_asset_max:
            applied.append("per_asset_max")
        if constraints.per_asset_min:
            applied.append("per_asset_min")
        if constraints.sector_caps:
            applied.append("sector_caps")
        if constraints.max_turnover is not None:
            applied.append(f"max_turnover={constraints.max_turnover:.2f}")
        if constraints.turnover_penalty > 0:
            applied.append(f"turnover_penalty={constraints.turnover_penalty:.4f}")
        return applied

    @staticmethod
    def _find_binding_constraints(
        *,
        weights: np.ndarray,
        lb: np.ndarray,
        ub: np.ndarray,
        sector_indices: dict[str, list[int]],
        sector_caps: dict[str, float],
        constraints_applied: list[str],
    ) -> list[str]:
        binding = []
        tol = 1e-4
        if abs(weights.sum() - 1.0) < tol:
            binding.append("sum_to_one")
        for i, (w, lo, u) in enumerate(zip(weights, lb, ub, strict=True)):
            if abs(w - lo) < tol and lo > 0:
                binding.append(f"lb[{i}]")
            if abs(w - u) < tol and u < 1.0:
                binding.append(f"ub[{i}]")
        for sector, cap in sector_caps.items():
            indices = sector_indices.get(sector, [])
            if indices and abs(float(np.sum(weights[indices])) - cap) < tol:
                binding.append(f"sector_cap:{sector}")
        return binding

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_inputs(cov: np.ndarray, mu: np.ndarray, assets: list[str]) -> str | None:
        if len(assets) == 0:
            return "No assets provided"
        if cov.shape != (len(assets), len(assets)):
            return f"Covariance shape {cov.shape} != ({len(assets)}, {len(assets)})"
        if not np.all(np.isfinite(cov)):
            return "Covariance matrix contains NaN or Inf values"
        if not np.all(np.isfinite(mu)):
            return "Expected returns vector contains NaN or Inf values"
        if not np.allclose(cov, cov.T, atol=1e-6):
            return "Covariance matrix is not symmetric"
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, result: OptimizationResult) -> None:
        row = OptimizerRunRow(
            run_id=result.run_id,
            objective=str(result.objective),
            solver_status=str(result.solver_status),
            fallback_mode=str(result.fallback_mode),
            generated_at=result.generated_at,
            dry_run=result.dry_run,
            asset_universe=result.assets,
            asset_count=len(result.assets),
            target_weights=result.target_weights,
            current_weights=result.current_weights,
            expected_return=result.expected_return,
            expected_volatility=result.expected_volatility,
            objective_value=result.objective_value,
            realized_turnover=result.realized_turnover,
            risk_aversion=result.risk_aversion,
            covariance_snapshot_id=result.covariance_snapshot_id,
            expected_return_source=result.expected_return_source,
            constraints_applied=result.constraints_applied,
            binding_constraints=result.binding_constraints,
            infeasibility_reason=result.infeasibility_reason,
            convergence_achieved=result.convergence_achieved,
            iterations_to_convergence=result.iterations_to_convergence,
            warnings=result.warnings,
            duration_seconds=result.duration_seconds,
            metadata_json=result.metadata,
        )
        self._run_repo.insert(row)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _emit_events(self, result: OptimizationResult) -> None:
        if result.solver_status == SolverStatus.FALLBACK_USED:
            logger.warning(
                "%s run_id=%s fallback=%s reason=%s",
                MVO_FALLBACK,
                result.run_id,
                result.fallback_mode,
                result.infeasibility_reason,
            )
        elif result.solver_status == SolverStatus.INFEASIBLE:
            logger.warning(
                "%s run_id=%s reason=%s",
                MVO_INFEASIBLE,
                result.run_id,
                result.infeasibility_reason,
            )
        else:
            logger.info(
                "%s run_id=%s status=%s ret=%.4f vol=%.4f turnover=%.4f converged=%s iter=%s dur=%.3f",
                MVO_COMPLETED,
                result.run_id,
                result.solver_status,
                result.expected_return or 0.0,
                result.expected_volatility or 0.0,
                result.realized_turnover or 0.0,
                result.convergence_achieved,
                result.iterations_to_convergence,
                result.duration_seconds,
            )

    def _record_metrics(self, result: OptimizationResult) -> None:
        attrs = {"objective": str(result.objective), "status": str(result.solver_status)}
        ratp_mvo_duration_seconds.record(result.duration_seconds, attrs)
        if result.expected_return is not None:
            ratp_mvo_expected_return.record(result.expected_return, attrs)
        if result.expected_volatility is not None:
            ratp_mvo_expected_volatility.record(result.expected_volatility, attrs)
        if result.realized_turnover is not None:
            ratp_mvo_turnover.record(result.realized_turnover, attrs)
        if result.solver_status == SolverStatus.INFEASIBLE:
            ratp_mvo_infeasible_total.add(1, attrs)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_latest_run(
        self, *, objective: OptimizationObjective | None = None, dry_run: bool | None = None
    ) -> OptimizerRunRow | None:
        return self._run_repo.get_latest(
            objective=str(objective) if objective is not None else None,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # Fallback / empty result builders
    # ------------------------------------------------------------------

    def _fallback_result(
        self,
        *,
        run_id: str,
        now: datetime,
        assets: list[str],
        current_weights: dict[str, float],
        reason: str,
        infeasibility_reason: str,
        snapshot_id: str | None,
        expected_return_source: str,
        warnings: list[str],
        duration: float,
    ) -> OptimizationResult:
        mode = self._config.fallback_mode
        if mode == FallbackMode.PREVIOUS_WEIGHTS:
            weights = {a: current_weights.get(a, 1.0 / max(len(assets), 1)) for a in assets}
        elif mode == FallbackMode.EQUAL_WEIGHTS:
            weights = {a: 1.0 / max(len(assets), 1) for a in assets}
        else:
            weights = {a: 1.0 / max(len(assets), 1) for a in assets}

        result = OptimizationResult(
            run_id=run_id,
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            solver_status=SolverStatus.FALLBACK_USED,
            assets=assets,
            target_weights=weights,
            current_weights={a: current_weights.get(a, 1.0 / max(len(assets), 1)) for a in assets},
            expected_return=None,
            expected_volatility=None,
            objective_value=None,
            realized_turnover=None,
            constraints_applied=[],
            binding_constraints=[],
            infeasibility_reason=infeasibility_reason,
            fallback_mode=mode,
            covariance_snapshot_id=snapshot_id,
            expected_return_source=expected_return_source,
            risk_aversion=self._config.risk_aversion,
            iterations_to_convergence=None,
            convergence_achieved=False,
            generated_at=now,
            duration_seconds=round(duration, 4),
            dry_run=self._config.dry_run,
            warnings=warnings + [reason],
        )
        if not self._config.dry_run:
            self._persist(result)
        self._emit_events(result)
        return result

    @staticmethod
    def _empty_result(*, run_id: str, now: datetime, duration: float) -> OptimizationResult:
        return OptimizationResult(
            run_id=run_id,
            objective=OptimizationObjective.MINIMUM_VARIANCE,
            solver_status=SolverStatus.FAILED,
            assets=[],
            target_weights={},
            current_weights={},
            constraints_applied=[],
            binding_constraints=[],
            infeasibility_reason="no_assets_provided",
            fallback_mode=FallbackMode.NONE,
            expected_return_source="none",
            risk_aversion=0.0,
            convergence_achieved=False,
            generated_at=now,
            duration_seconds=round(duration, 4),
            dry_run=True,
            warnings=["No assets provided"],
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_inputs(assets: list[str], mu: np.ndarray, cov: np.ndarray) -> str:
        data = json.dumps(
            {"assets": assets, "mu": mu.tolist(), "cov": cov.tolist()}, sort_keys=True
        )
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    @staticmethod
    def result_to_jsonable(result: OptimizationResult) -> dict[str, Any]:
        return dict(result.model_dump(mode="json"))
