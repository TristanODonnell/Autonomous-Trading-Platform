from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import uuid4

import numpy as np

from autonomous_trading_platform.contracts.runtime.optimization_backend import (
    OptimizationBackendConfig,
    OptimizationBackendConstraintType,
    OptimizationBackendObjectiveType,
    OptimizationBackendResult,
    OptimizationBackendStatus,
    OptimizationConstraintTerm,
    OptimizationFallbackPolicy,
    OptimizationObjectiveTerm,
    OptimizationProblem,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_optimizer_constraint_binding_total,
    ratp_optimizer_duration_seconds,
    ratp_optimizer_fallback_total,
    ratp_optimizer_infeasible_total,
    ratp_optimizer_runs_total,
)

logger = get_logger(__name__)

OPTIMIZATION_STARTED = "OPTIMIZATION_STARTED"
OPTIMIZATION_COMPLETED = "OPTIMIZATION_COMPLETED"
OPTIMIZATION_INFEASIBLE = "OPTIMIZATION_INFEASIBLE"
OPTIMIZATION_FALLBACK_USED = "OPTIMIZATION_FALLBACK_USED"
OPTIMIZATION_NUMERICAL_REPAIR_APPLIED = "OPTIMIZATION_NUMERICAL_REPAIR_APPLIED"

_MIN_SUM_GAP = 1e-8


class OptimizationInfeasibleError(ValueError):
    """Raised when a convex optimization problem has no feasible solution."""


class OptimizerBackend(Protocol):
    def solve(self, problem: OptimizationProblem) -> OptimizationBackendResult:
        """Solve an optimization problem and return audited backend diagnostics."""


class CvxpyOptimizerBackend:
    """CVXPY-first optimization backend with deterministic fallback support.

    CVXPY-specific logic is contained in this backend. Callers pass explicit
    objectives and constraints and receive a structured result regardless of
    solver success, infeasibility, or fallback.
    """

    def __init__(self, config: OptimizationBackendConfig | None = None) -> None:
        self._config = config or OptimizationBackendConfig()

    def solve(self, problem: OptimizationProblem) -> OptimizationBackendResult:
        run_id = str(uuid4())
        start = perf_counter()
        generated_at = datetime.now(UTC)
        input_hash = _hash_problem(problem)

        logger.info(
            "%s run_id=%s backend=cvxpy problem_type=%s assets=%d",
            OPTIMIZATION_STARTED,
            run_id,
            problem.problem_type,
            len(problem.assets),
        )
        ratp_optimizer_runs_total.add(1, {"backend": "cvxpy", "problem_type": problem.problem_type})

        validation_error = _validate_problem(problem)
        if validation_error is not None:
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason=validation_error,
                start=start,
                status=OptimizationBackendStatus.INFEASIBLE,
            )

        assets = sorted(problem.assets)
        cov, repairs, condition_number = _prepare_covariance(
            problem=problem,
            assets=assets,
            config=self._config,
        )
        if cov is None:
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason="missing_or_invalid_covariance_matrix",
                start=start,
                status=OptimizationBackendStatus.INFEASIBLE,
                repairs=repairs,
                condition_number=condition_number,
            )

        for repair in repairs:
            logger.warning(
                "%s run_id=%s repair=%s condition_number=%s",
                OPTIMIZATION_NUMERICAL_REPAIR_APPLIED,
                run_id,
                repair,
                condition_number,
            )

        try:
            import cvxpy as cp  # type: ignore[import-not-found]
        except Exception:
            if self._config.allow_numpy_fallback:
                result = NumpyConvexOptimizerBackend(self._config).solve(
                    problem.model_copy(
                        update={"metadata": {**problem.metadata, "cvxpy_unavailable": True}}
                    )
                )
                result.backend_name = "numpy_projected_gradient"
                result.metadata["cvxpy_unavailable"] = True
                return result
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason="cvxpy_unavailable",
                start=start,
                status=OptimizationBackendStatus.FAILED,
                repairs=repairs,
                condition_number=condition_number,
            )

        n = len(assets)
        weights = cp.Variable(n)
        mu = np.array([problem.expected_returns.get(asset, 0.0) for asset in assets], dtype=float)
        current = np.array(
            [problem.current_weights.get(asset, 1.0 / max(n, 1)) for asset in assets],
            dtype=float,
        )
        current = _normalize_array(current)

        objective_expr = self._build_cvxpy_objective(
            cp=cp,
            weights=weights,
            assets=assets,
            cov=cov,
            mu=mu,
            current=current,
            objectives=problem.objectives,
        )
        constraints = self._build_cvxpy_constraints(
            cp=cp,
            weights=weights,
            assets=assets,
            current=current,
            constraints=problem.constraints,
        )

        cp_problem = cp.Problem(cp.Minimize(objective_expr), constraints)
        solver_errors: list[str] = []
        solver_used: str | None = None
        for solver_name in self._solver_order():
            try:
                solver_used = solver_name
                kwargs: dict[str, Any] = {}
                if solver_name.upper() in {"SCS", "OSQP", "CLARABEL"}:
                    kwargs["max_iter"] = self._config.max_iterations
                cp_problem.solve(solver=solver_name, verbose=False, **kwargs)
                if cp_problem.status in {"optimal", "optimal_inaccurate"}:
                    break
            except Exception as exc:
                solver_errors.append(f"{solver_name}: {exc}")

        if cp_problem.status not in {"optimal", "optimal_inaccurate"} or weights.value is None:
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason=f"solver_status={cp_problem.status}; errors={solver_errors}",
                start=start,
                status=OptimizationBackendStatus.INFEASIBLE,
                solver_used=solver_used,
                repairs=repairs,
                condition_number=condition_number,
            )

        raw = np.array(weights.value, dtype=float).reshape(n)
        target = _weights_dict(assets, _project_simplex(raw))
        result = self._success_result(
            run_id=run_id,
            generated_at=generated_at,
            problem=problem,
            input_hash=input_hash,
            assets=assets,
            weights=target,
            solver_used=solver_used,
            status=(
                OptimizationBackendStatus.OPTIMAL
                if cp_problem.status == "optimal"
                else OptimizationBackendStatus.SUBOPTIMAL
            ),
            start=start,
            repairs=repairs,
            condition_number=condition_number,
        )
        self._emit_result(result)
        return result

    def _solver_order(self) -> list[str]:
        ordered = [self._config.solver_name]
        ordered.extend(s for s in self._config.fallback_solver_order if s not in ordered)
        return ordered

    @staticmethod
    def _build_cvxpy_objective(
        *,
        cp: Any,
        weights: Any,
        assets: list[str],
        cov: np.ndarray,
        mu: np.ndarray,
        current: np.ndarray,
        objectives: list[OptimizationObjectiveTerm],
    ) -> Any:
        expr = 0
        for term in objectives:
            if term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_VARIANCE:
                expr += term.weight * cp.quad_form(weights, cov)
            elif term.objective_type == OptimizationBackendObjectiveType.MAXIMIZE_EXPECTED_RETURN:
                expr += -term.weight * (mu @ weights)
            elif term.objective_type == OptimizationBackendObjectiveType.MAXIMIZE_UTILITY:
                expr += term.weight * (
                    term.risk_aversion * cp.quad_form(weights, cov) - mu @ weights
                )
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_TURNOVER:
                expr += term.weight * cp.norm1(weights - current)
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_TRACKING_ERROR:
                benchmark = np.array(
                    [term.benchmark_weights.get(asset, 0.0) for asset in assets],
                    dtype=float,
                )
                expr += term.weight * cp.quad_form(weights - benchmark, cov)
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_FACTOR_EXPOSURE:
                for factor_name in _factor_names(term.factor_exposures):
                    factor = np.array(
                        [
                            term.factor_exposures.get(asset, {}).get(factor_name, 0.0)
                            for asset in assets
                        ],
                        dtype=float,
                    )
                    expr += term.weight * cp.square(factor @ weights)
        return expr

    @staticmethod
    def _build_cvxpy_constraints(
        *,
        cp: Any,
        weights: Any,
        assets: list[str],
        current: np.ndarray,
        constraints: list[OptimizationConstraintTerm],
    ) -> list[Any]:
        built = []
        for constraint in constraints:
            if constraint.constraint_type == OptimizationBackendConstraintType.WEIGHTS_SUM_TO:
                built.append(
                    cp.sum(weights) == (constraint.value if constraint.value is not None else 1.0)
                )
            elif constraint.constraint_type == OptimizationBackendConstraintType.LONG_ONLY:
                built.append(weights >= 0)
            elif constraint.constraint_type == OptimizationBackendConstraintType.MIN_WEIGHT:
                if constraint.per_asset:
                    for i, asset in enumerate(assets):
                        built.append(
                            weights[i] >= constraint.per_asset.get(asset, constraint.value or 0.0)
                        )
                else:
                    built.append(
                        weights >= (constraint.value if constraint.value is not None else 0.0)
                    )
            elif constraint.constraint_type == OptimizationBackendConstraintType.MAX_WEIGHT:
                if constraint.per_asset:
                    for i, asset in enumerate(assets):
                        built.append(
                            weights[i] <= constraint.per_asset.get(asset, constraint.value or 1.0)
                        )
                else:
                    built.append(
                        weights <= (constraint.value if constraint.value is not None else 1.0)
                    )
            elif constraint.constraint_type == OptimizationBackendConstraintType.CASH_RESERVE:
                built.append(cp.sum(weights) <= 1.0 - (constraint.value or 0.0))
            elif constraint.constraint_type == OptimizationBackendConstraintType.TURNOVER_LIMIT:
                built.append(cp.norm1(weights - current) <= (constraint.value or 0.0))
            elif constraint.constraint_type == OptimizationBackendConstraintType.SECTOR_CAP:
                for sector, cap in constraint.sector_caps.items():
                    idx = [
                        i
                        for i, asset in enumerate(assets)
                        if constraint.sector_map.get(asset) == sector
                    ]
                    if idx:
                        built.append(cp.sum(weights[idx]) <= cap)
            elif constraint.constraint_type == OptimizationBackendConstraintType.TARGET_RETURN:
                returns = np.array(
                    [constraint.per_asset.get(asset, 0.0) for asset in assets],
                    dtype=float,
                )
                built.append(returns @ weights >= (constraint.value or 0.0))
            elif (
                constraint.constraint_type
                == OptimizationBackendConstraintType.FACTOR_EXPOSURE_BOUND
            ):
                factor_name = constraint.factor_name
                if factor_name:
                    factor = np.array(
                        [
                            constraint.factor_exposures.get(asset, {}).get(factor_name, 0.0)
                            for asset in assets
                        ],
                        dtype=float,
                    )
                    if constraint.lower is not None:
                        built.append(factor @ weights >= constraint.lower)
                    if constraint.upper is not None:
                        built.append(factor @ weights <= constraint.upper)
        return built

    def _success_result(
        self,
        *,
        run_id: str,
        generated_at: datetime,
        problem: OptimizationProblem,
        input_hash: str,
        assets: list[str],
        weights: dict[str, float],
        solver_used: str | None,
        status: OptimizationBackendStatus,
        start: float,
        repairs: list[str],
        condition_number: float | None,
    ) -> OptimizationBackendResult:
        result = OptimizationBackendResult(
            optimizer_run_id=run_id,
            generated_at=generated_at,
            backend_name="cvxpy",
            solver_used=solver_used,
            solver_status=status,
            problem_type=problem.problem_type,
            objective_summary=[str(term.objective_type) for term in problem.objectives],
            constraint_summary=[str(term.constraint_type) for term in problem.constraints],
            assets=assets,
            target_weights=weights,
            fallback_used=False,
            fallback_policy=self._config.fallback_policy,
            binding_constraints=_binding_constraints(problem, weights),
            covariance_snapshot_id=problem.covariance_snapshot_id,
            factor_snapshot_id=problem.factor_snapshot_id,
            expected_return_source=problem.expected_return_source,
            input_hash=input_hash,
            repairs_applied=repairs,
            condition_number=condition_number,
            duration_seconds=round(perf_counter() - start, 4),
            metadata=problem.metadata,
        )
        return result

    def _fallback_result(
        self,
        *,
        run_id: str,
        generated_at: datetime,
        problem: OptimizationProblem,
        input_hash: str,
        reason: str,
        start: float,
        status: OptimizationBackendStatus,
        solver_used: str | None = None,
        repairs: list[str] | None = None,
        condition_number: float | None = None,
    ) -> OptimizationBackendResult:
        weights = _fallback_weights(problem, self._config.fallback_policy)
        result = OptimizationBackendResult(
            optimizer_run_id=run_id,
            generated_at=generated_at,
            backend_name="cvxpy",
            solver_used=solver_used,
            solver_status=(
                OptimizationBackendStatus.FALLBACK_USED
                if self._config.fallback_policy != OptimizationFallbackPolicy.NONE
                else status
            ),
            problem_type=problem.problem_type,
            objective_summary=[str(term.objective_type) for term in problem.objectives],
            constraint_summary=[str(term.constraint_type) for term in problem.constraints],
            assets=sorted(problem.assets),
            target_weights=weights,
            fallback_used=self._config.fallback_policy != OptimizationFallbackPolicy.NONE,
            fallback_policy=self._config.fallback_policy,
            infeasibility_reason=reason,
            violated_constraints=[reason],
            covariance_snapshot_id=problem.covariance_snapshot_id,
            factor_snapshot_id=problem.factor_snapshot_id,
            expected_return_source=problem.expected_return_source,
            input_hash=input_hash,
            repairs_applied=repairs or [],
            condition_number=condition_number,
            duration_seconds=round(perf_counter() - start, 4),
            metadata=problem.metadata,
        )
        self._emit_result(result)
        return result

    @staticmethod
    def _emit_result(result: OptimizationBackendResult) -> None:
        attrs = {"backend": result.backend_name, "status": str(result.solver_status)}
        ratp_optimizer_duration_seconds.record(result.duration_seconds, attrs)
        if result.solver_status == OptimizationBackendStatus.FALLBACK_USED:
            ratp_optimizer_fallback_total.add(1, attrs)
            logger.warning(
                "%s run_id=%s reason=%s fallback=%s",
                OPTIMIZATION_FALLBACK_USED,
                result.optimizer_run_id,
                result.infeasibility_reason,
                result.fallback_policy,
            )
        elif result.solver_status == OptimizationBackendStatus.INFEASIBLE:
            ratp_optimizer_infeasible_total.add(1, attrs)
            logger.warning(
                "%s run_id=%s reason=%s",
                OPTIMIZATION_INFEASIBLE,
                result.optimizer_run_id,
                result.infeasibility_reason,
            )
        else:
            logger.info(
                "%s run_id=%s solver=%s status=%s duration=%.4f",
                OPTIMIZATION_COMPLETED,
                result.optimizer_run_id,
                result.solver_used,
                result.solver_status,
                result.duration_seconds,
            )
        if result.binding_constraints:
            ratp_optimizer_constraint_binding_total.add(len(result.binding_constraints), attrs)


class NumpyConvexOptimizerBackend:
    """Deterministic projected-gradient fallback implementing the same abstraction."""

    def __init__(self, config: OptimizationBackendConfig | None = None) -> None:
        self._config = config or OptimizationBackendConfig()

    def solve(self, problem: OptimizationProblem) -> OptimizationBackendResult:
        run_id = str(uuid4())
        start = perf_counter()
        generated_at = datetime.now(UTC)
        input_hash = _hash_problem(problem)
        assets = sorted(problem.assets)
        ratp_optimizer_runs_total.add(
            1,
            {"backend": "numpy_projected_gradient", "problem_type": problem.problem_type},
        )

        validation_error = _validate_problem(problem)
        if validation_error is not None:
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason=validation_error,
                start=start,
            )

        cov, repairs, condition_number = _prepare_covariance(
            problem=problem,
            assets=assets,
            config=self._config,
        )
        if cov is None:
            return self._fallback_result(
                run_id=run_id,
                generated_at=generated_at,
                problem=problem,
                input_hash=input_hash,
                reason="missing_or_invalid_covariance_matrix",
                start=start,
                repairs=repairs,
                condition_number=condition_number,
            )

        n = len(assets)
        mu = np.array([problem.expected_returns.get(asset, 0.0) for asset in assets], dtype=float)
        current = _normalize_array(
            np.array(
                [problem.current_weights.get(asset, 1.0 / max(n, 1)) for asset in assets],
                dtype=float,
            )
        )
        w = current.copy()
        step = 0.05 / max(float(np.linalg.norm(cov)) + 1.0, 1.0)

        for _ in range(self._config.max_iterations):
            previous = w.copy()
            grad = self._gradient(
                assets=assets,
                weights=w,
                current=current,
                cov=cov,
                mu=mu,
                objectives=problem.objectives,
            )
            w = _project_simplex(w - step * grad)
            w = _apply_backend_constraints(
                assets=assets, weights=w, constraints=problem.constraints
            )
            if float(np.max(np.abs(w - previous))) < self._config.tolerance:
                break

        weights = _weights_dict(assets, w)
        result = OptimizationBackendResult(
            optimizer_run_id=run_id,
            generated_at=generated_at,
            backend_name="numpy_projected_gradient",
            solver_used="projected_gradient",
            solver_status=OptimizationBackendStatus.SUBOPTIMAL,
            problem_type=problem.problem_type,
            objective_summary=[str(term.objective_type) for term in problem.objectives],
            constraint_summary=[str(term.constraint_type) for term in problem.constraints],
            assets=assets,
            target_weights=weights,
            fallback_used=False,
            fallback_policy=self._config.fallback_policy,
            binding_constraints=_binding_constraints(problem, weights),
            covariance_snapshot_id=problem.covariance_snapshot_id,
            factor_snapshot_id=problem.factor_snapshot_id,
            expected_return_source=problem.expected_return_source,
            input_hash=input_hash,
            repairs_applied=repairs,
            condition_number=condition_number,
            duration_seconds=round(perf_counter() - start, 4),
            metadata=problem.metadata,
        )
        CvxpyOptimizerBackend._emit_result(result)
        return result

    @staticmethod
    def _gradient(
        *,
        assets: list[str],
        weights: np.ndarray,
        current: np.ndarray,
        cov: np.ndarray,
        mu: np.ndarray,
        objectives: list[OptimizationObjectiveTerm],
    ) -> np.ndarray:
        grad = np.zeros(len(assets))
        for term in objectives:
            if term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_VARIANCE:
                grad += term.weight * 2.0 * (cov @ weights)
            elif term.objective_type == OptimizationBackendObjectiveType.MAXIMIZE_EXPECTED_RETURN:
                grad += -term.weight * mu
            elif term.objective_type == OptimizationBackendObjectiveType.MAXIMIZE_UTILITY:
                grad += term.weight * (2.0 * term.risk_aversion * (cov @ weights) - mu)
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_TURNOVER:
                grad += term.weight * np.sign(weights - current)
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_TRACKING_ERROR:
                benchmark = np.array(
                    [term.benchmark_weights.get(asset, 0.0) for asset in assets],
                    dtype=float,
                )
                grad += term.weight * 2.0 * (cov @ (weights - benchmark))
            elif term.objective_type == OptimizationBackendObjectiveType.MINIMIZE_FACTOR_EXPOSURE:
                for factor_name in _factor_names(term.factor_exposures):
                    factor = np.array(
                        [
                            term.factor_exposures.get(asset, {}).get(factor_name, 0.0)
                            for asset in assets
                        ],
                        dtype=float,
                    )
                    exposure = float(factor @ weights)
                    grad += term.weight * 2.0 * exposure * factor
        return grad

    def _fallback_result(
        self,
        *,
        run_id: str,
        generated_at: datetime,
        problem: OptimizationProblem,
        input_hash: str,
        reason: str,
        start: float,
        repairs: list[str] | None = None,
        condition_number: float | None = None,
    ) -> OptimizationBackendResult:
        result = CvxpyOptimizerBackend(self._config)._fallback_result(
            run_id=run_id,
            generated_at=generated_at,
            problem=problem,
            input_hash=input_hash,
            reason=reason,
            start=start,
            status=OptimizationBackendStatus.INFEASIBLE,
            solver_used="projected_gradient",
            repairs=repairs,
            condition_number=condition_number,
        )
        result.backend_name = "numpy_projected_gradient"
        return result


def _validate_problem(problem: OptimizationProblem) -> str | None:
    if not problem.assets:
        return "no_assets_provided"
    if len(problem.assets) != len(set(problem.assets)):
        return "duplicate_assets_provided"
    if not problem.objectives:
        return "no_objectives_provided"
    for asset, value in problem.expected_returns.items():
        if asset in problem.assets and not math.isfinite(value):
            return f"non_finite_expected_return:{asset}"
    if problem.covariance_matrix is not None:
        for asset in problem.assets:
            if asset not in problem.covariance_matrix:
                return f"missing_covariance_row:{asset}"
            for other in problem.assets:
                cov_value = problem.covariance_matrix[asset].get(other)
                if cov_value is None or not math.isfinite(cov_value):
                    return f"invalid_covariance_value:{asset}:{other}"
    lb = 0.0
    ub = 1.0
    for constraint in problem.constraints:
        if constraint.constraint_type == OptimizationBackendConstraintType.MIN_WEIGHT:
            min_weight_value = constraint.value
            min_weight = 0.0 if min_weight_value is None else float(min_weight_value)
            lb = max(lb, min_weight)
        if constraint.constraint_type == OptimizationBackendConstraintType.MAX_WEIGHT:
            max_weight_value = constraint.value
            max_weight = 1.0 if max_weight_value is None else float(max_weight_value)
            ub = min(ub, max_weight)
    if lb > ub:
        return "contradictory_weight_bounds"
    if lb * len(problem.assets) > 1.0 + _MIN_SUM_GAP:
        return "minimum_weights_exceed_full_allocation"
    if ub * len(problem.assets) < 1.0 - _MIN_SUM_GAP:
        return "maximum_weights_below_full_allocation"
    return None


def _prepare_covariance(
    *,
    problem: OptimizationProblem,
    assets: list[str],
    config: OptimizationBackendConfig,
) -> tuple[np.ndarray | None, list[str], float | None]:
    repairs: list[str] = []
    n = len(assets)
    if problem.covariance_matrix is None:
        cov = np.eye(n) * config.diagonal_jitter
        repairs.append("identity_covariance_fallback")
    else:
        cov = np.array(
            [[problem.covariance_matrix[a][b] for b in assets] for a in assets],
            dtype=float,
        )
    if not np.all(np.isfinite(cov)):
        return None, repairs, None
    if not np.allclose(cov, cov.T, atol=1e-8):
        cov = (cov + cov.T) * 0.5
        repairs.append("symmetrized_covariance")
    cov = cov + np.eye(n) * config.covariance_regularization
    if config.covariance_regularization > 0:
        repairs.append("diagonal_regularization")
    try:
        eigenvalues = np.linalg.eigvalsh(cov)
        if np.min(eigenvalues) < config.diagonal_jitter and config.repair_psd:
            cov += np.eye(n) * (abs(float(np.min(eigenvalues))) + config.diagonal_jitter)
            repairs.append("psd_repair")
        condition_number = float(np.linalg.cond(cov))
    except np.linalg.LinAlgError:
        return None, repairs, None
    return cov, repairs, condition_number


def _fallback_weights(
    problem: OptimizationProblem,
    policy: OptimizationFallbackPolicy,
) -> dict[str, float]:
    assets = sorted(problem.assets)
    if not assets:
        return {}
    if policy in {
        OptimizationFallbackPolicy.PREVIOUS_WEIGHTS,
        OptimizationFallbackPolicy.HOLD_CURRENT,
    }:
        return _normalize_dict({asset: problem.current_weights.get(asset, 0.0) for asset in assets})
    if policy == OptimizationFallbackPolicy.QUALITY_WEIGHTED and problem.quality_scores:
        return _normalize_dict(
            {asset: max(problem.quality_scores.get(asset, 0.0), 0.0) for asset in assets}
        )
    if policy == OptimizationFallbackPolicy.NONE:
        return {}
    equal = 1.0 / len(assets)
    return {asset: equal for asset in assets}


def _apply_backend_constraints(
    *,
    assets: list[str],
    weights: np.ndarray,
    constraints: list[OptimizationConstraintTerm],
) -> np.ndarray:
    w = weights.copy()
    for constraint in constraints:
        if constraint.constraint_type == OptimizationBackendConstraintType.MAX_WEIGHT:
            cap = constraint.value if constraint.value is not None else 1.0
            caps = np.array([constraint.per_asset.get(asset, cap) for asset in assets])
            w = _project_with_upper_bounds(w, caps)
        elif constraint.constraint_type == OptimizationBackendConstraintType.MIN_WEIGHT:
            floor = constraint.value if constraint.value is not None else 0.0
            floors = np.array([constraint.per_asset.get(asset, floor) for asset in assets])
            w = _project_with_lower_bounds(w, floors)
        elif constraint.constraint_type == OptimizationBackendConstraintType.SECTOR_CAP:
            for sector, cap in constraint.sector_caps.items():
                idx = [
                    i
                    for i, asset in enumerate(assets)
                    if constraint.sector_map.get(asset) == sector
                ]
                total = float(np.sum(w[idx])) if idx else 0.0
                if total > cap and total > 0:
                    w = _project_sector_cap(w, idx, cap)
    return w


def _binding_constraints(problem: OptimizationProblem, weights: dict[str, float]) -> list[str]:
    binding: list[str] = []
    for constraint in problem.constraints:
        if constraint.constraint_type == OptimizationBackendConstraintType.WEIGHTS_SUM_TO:
            target = constraint.value if constraint.value is not None else 1.0
            if abs(sum(weights.values()) - target) < 1e-4:
                binding.append("weights_sum_to")
        elif constraint.constraint_type == OptimizationBackendConstraintType.MAX_WEIGHT:
            cap = constraint.value if constraint.value is not None else 1.0
            for asset, weight in weights.items():
                if abs(weight - constraint.per_asset.get(asset, cap)) < 1e-4:
                    binding.append(f"max_weight:{asset}")
        elif constraint.constraint_type == OptimizationBackendConstraintType.SECTOR_CAP:
            for sector, cap in constraint.sector_caps.items():
                total = sum(
                    weight
                    for asset, weight in weights.items()
                    if constraint.sector_map.get(asset) == sector
                )
                if abs(total - cap) < 1e-4:
                    binding.append(f"sector_cap:{sector}")
    return binding


def _project_simplex(values: np.ndarray) -> np.ndarray:
    v = np.maximum(values, 0.0)
    if float(np.sum(v)) <= _MIN_SUM_GAP:
        return np.ones(len(v)) / max(len(v), 1)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    rho_candidates = u * np.arange(1, len(u) + 1) > (cssv - 1.0)
    if not np.any(rho_candidates):
        return v / float(np.sum(v))
    rho = int(np.nonzero(rho_candidates)[0][-1])
    theta = (cssv[rho] - 1.0) / float(rho + 1)
    return np.maximum(v - theta, 0.0)


def _project_with_upper_bounds(values: np.ndarray, upper: np.ndarray) -> np.ndarray:
    capped = np.minimum(np.maximum(values, 0.0), upper)
    for _ in range(20):
        deficit = 1.0 - float(np.sum(capped))
        if abs(deficit) <= 1e-10:
            break
        if deficit > 0:
            room = np.maximum(upper - capped, 0.0)
            room_total = float(np.sum(room))
            if room_total <= _MIN_SUM_GAP:
                break
            capped += deficit * room / room_total
        else:
            reducible = np.maximum(capped, 0.0)
            reducible_total = float(np.sum(reducible))
            if reducible_total <= _MIN_SUM_GAP:
                break
            capped += deficit * reducible / reducible_total
        capped = np.minimum(np.maximum(capped, 0.0), upper)
    return capped


def _project_with_lower_bounds(values: np.ndarray, lower: np.ndarray) -> np.ndarray:
    if float(np.sum(lower)) >= 1.0:
        return lower / float(np.sum(lower))
    residual = _project_simplex(np.maximum(values - lower, 0.0))
    return lower + residual * (1.0 - float(np.sum(lower)))


def _project_sector_cap(values: np.ndarray, sector_indices: list[int], cap: float) -> np.ndarray:
    w = values.copy()
    sector_total = float(np.sum(w[sector_indices]))
    if sector_total <= cap or sector_total <= _MIN_SUM_GAP:
        return w
    excess = sector_total - cap
    for i in sector_indices:
        w[i] *= cap / sector_total
    outside = [i for i in range(len(w)) if i not in set(sector_indices)]
    outside_total = float(np.sum(w[outside])) if outside else 0.0
    if outside and outside_total > _MIN_SUM_GAP:
        for i in outside:
            w[i] += excess * (w[i] / outside_total)
    elif outside:
        for i in outside:
            w[i] += excess / len(outside)
    return _project_simplex(w)


def _normalize_array(values: np.ndarray) -> np.ndarray:
    clipped = np.maximum(values, 0.0)
    total = float(np.sum(clipped))
    if total <= _MIN_SUM_GAP:
        return np.ones(len(values)) / max(len(values), 1)
    return clipped / total


def _normalize_dict(values: dict[str, float]) -> dict[str, float]:
    total = sum(max(v, 0.0) for v in values.values())
    if total <= _MIN_SUM_GAP:
        equal = 1.0 / max(len(values), 1)
        return {key: equal for key in values}
    return {key: max(value, 0.0) / total for key, value in values.items()}


def _weights_dict(assets: list[str], weights: np.ndarray) -> dict[str, float]:
    return {asset: round(float(weights[i]), 10) for i, asset in enumerate(assets)}


def _factor_names(factor_exposures: dict[str, dict[str, float]]) -> list[str]:
    names = sorted({name for payload in factor_exposures.values() for name in payload})
    return names


def _hash_problem(problem: OptimizationProblem) -> str:
    payload = cast(dict[str, Any], problem.model_dump(mode="json"))
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
