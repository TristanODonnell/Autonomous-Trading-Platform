from __future__ import annotations

import math

from autonomous_trading_platform.application.services.mean_variance_optimizer import (
    MeanVarianceConfig,
    MeanVarianceOptimizer,
)
from autonomous_trading_platform.application.services.optimization_backend import (
    NumpyConvexOptimizerBackend,
)
from autonomous_trading_platform.contracts.runtime.optimization_backend import (
    OptimizationBackendConfig,
    OptimizationBackendConstraintType,
    OptimizationBackendObjectiveType,
    OptimizationBackendStatus,
    OptimizationConstraintTerm,
    OptimizationFallbackPolicy,
    OptimizationObjectiveTerm,
    OptimizationProblem,
)
from autonomous_trading_platform.contracts.runtime.optimizer import (
    OptimizationObjective,
    OptimizerConstraints,
)


def _problem() -> OptimizationProblem:
    assets = ["b", "a", "c"]
    cov = {
        "a": {"a": 0.01, "b": 0.0, "c": 0.0},
        "b": {"a": 0.0, "b": 0.04, "c": 0.0},
        "c": {"a": 0.0, "b": 0.0, "c": 0.09},
    }
    return OptimizationProblem(
        problem_type="unit_test",
        assets=assets,
        covariance_matrix=cov,
        expected_returns={"a": 0.05, "b": 0.10, "c": 0.20},
        current_weights={"a": 0.4, "b": 0.4, "c": 0.2},
        objectives=[
            OptimizationObjectiveTerm(
                objective_type=OptimizationBackendObjectiveType.MINIMIZE_VARIANCE
            )
        ],
        constraints=[
            OptimizationConstraintTerm(
                constraint_type=OptimizationBackendConstraintType.WEIGHTS_SUM_TO,
                value=1.0,
            ),
            OptimizationConstraintTerm(constraint_type=OptimizationBackendConstraintType.LONG_ONLY),
        ],
        dry_run=True,
        covariance_snapshot_id="cov-1",
    )


def test_successful_backend_optimization_weights_sum_to_one() -> None:
    result = NumpyConvexOptimizerBackend().solve(_problem())

    assert result.solver_status == OptimizationBackendStatus.SUBOPTIMAL
    assert math.isclose(sum(result.target_weights.values()), 1.0, rel_tol=1e-6)
    assert all(weight >= -1e-9 for weight in result.target_weights.values())
    assert result.covariance_snapshot_id == "cov-1"


def test_allocation_cap_constraint_respected() -> None:
    problem = _problem().model_copy(
        update={
            "constraints": _problem().constraints
            + [
                OptimizationConstraintTerm(
                    constraint_type=OptimizationBackendConstraintType.MAX_WEIGHT,
                    value=0.50,
                )
            ]
        }
    )
    result = NumpyConvexOptimizerBackend().solve(problem)

    assert all(weight <= 0.50 + 1e-6 for weight in result.target_weights.values())


def test_sector_cap_constraint_reports_binding_or_respects_cap() -> None:
    problem = _problem().model_copy(
        update={
            "constraints": _problem().constraints
            + [
                OptimizationConstraintTerm(
                    constraint_type=OptimizationBackendConstraintType.SECTOR_CAP,
                    sector_map={"a": "tech", "b": "tech", "c": "finance"},
                    sector_caps={"tech": 0.60},
                )
            ]
        }
    )
    result = NumpyConvexOptimizerBackend().solve(problem)

    tech_weight = result.target_weights["a"] + result.target_weights["b"]
    assert tech_weight <= 0.60 + 1e-6


def test_factor_exposure_objective_reduces_factor_loading() -> None:
    problem = _problem().model_copy(
        update={
            "objectives": [
                OptimizationObjectiveTerm(
                    objective_type=OptimizationBackendObjectiveType.MINIMIZE_VARIANCE,
                    weight=0.1,
                ),
                OptimizationObjectiveTerm(
                    objective_type=OptimizationBackendObjectiveType.MINIMIZE_FACTOR_EXPOSURE,
                    weight=5.0,
                    factor_exposures={
                        "a": {"market_beta": -0.5},
                        "b": {"market_beta": 1.5},
                        "c": {"market_beta": 2.0},
                    },
                ),
            ]
        }
    )
    result = NumpyConvexOptimizerBackend().solve(problem)
    beta = (
        -0.5 * result.target_weights["a"]
        + 1.5 * result.target_weights["b"]
        + 2.0 * result.target_weights["c"]
    )

    assert abs(beta) < 0.75


def test_infeasible_constraints_use_explicit_fallback() -> None:
    problem = _problem().model_copy(
        update={
            "constraints": [
                OptimizationConstraintTerm(
                    constraint_type=OptimizationBackendConstraintType.MIN_WEIGHT,
                    value=0.50,
                )
            ]
        }
    )
    result = NumpyConvexOptimizerBackend(
        OptimizationBackendConfig(fallback_policy=OptimizationFallbackPolicy.EQUAL_WEIGHTS)
    ).solve(problem)

    assert result.fallback_used is True
    assert result.infeasibility_reason == "minimum_weights_exceed_full_allocation"


def test_covariance_repair_metadata_recorded() -> None:
    problem = _problem().model_copy(
        update={
            "covariance_matrix": {
                "a": {"a": 0.01, "b": 0.02, "c": 0.0},
                "b": {"a": 0.00, "b": 0.04, "c": 0.0},
                "c": {"a": 0.00, "b": 0.00, "c": 0.09},
            }
        }
    )
    result = NumpyConvexOptimizerBackend().solve(problem)

    assert "symmetrized_covariance" in result.repairs_applied
    assert "diagonal_regularization" in result.repairs_applied
    assert result.condition_number is not None


def test_backend_orders_assets_deterministically() -> None:
    result = NumpyConvexOptimizerBackend().solve(_problem())

    assert result.assets == ["a", "b", "c"]
    assert list(result.target_weights.keys()) == ["a", "b", "c"]


def test_mvo_can_opt_into_convex_backend(db_session) -> None:
    assets = ["a", "b"]
    cov = {"a": {"a": 0.01, "b": 0.0}, "b": {"a": 0.0, "b": 0.04}}
    optimizer = MeanVarianceOptimizer(
        session=db_session,
        config=MeanVarianceConfig(use_convex_backend=True, dry_run=True),
        optimizer_backend=NumpyConvexOptimizerBackend(),
    )

    result = optimizer.optimize(
        assets=assets,
        expected_returns={"a": 0.05, "b": 0.10},
        objective=OptimizationObjective.MINIMUM_VARIANCE,
        constraints=OptimizerConstraints(global_max_weight=0.80),
        covariance_matrix=cov,
        covariance_snapshot_id="provided-cov",
    )

    assert result.metadata["backend_result"]["backend_name"] == "numpy_projected_gradient"
    assert math.isclose(sum(result.target_weights.values()), 1.0, rel_tol=1e-6)
