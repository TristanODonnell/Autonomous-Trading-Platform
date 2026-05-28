from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.factor_neutralization_service import (
    FactorNeutralizationConfig,
    FactorNeutralizationService,
)
from autonomous_trading_platform.contracts.runtime.factor_neutralization import (
    FactorConstraintType,
    FactorNeutralizationConstraint,
    FactorNeutralizationMode,
    FactorNeutralizationRequest,
    FactorNeutralizationStatus,
)
from autonomous_trading_platform.contracts.runtime.optimizer import OptimizerConstraints
from autonomous_trading_platform.storage.sor.repositories.core.factor_neutralization_repository import (
    FactorNeutralizationRepository,
)


def _request() -> FactorNeutralizationRequest:
    return FactorNeutralizationRequest(
        assets=["high_beta", "hedge"],
        current_weights={"high_beta": 0.80, "hedge": 0.20},
        factor_exposures={
            "high_beta": {"market_beta": 1.5, "momentum": 0.30, "volatility": 0.40, "size": 0.2},
            "hedge": {"market_beta": -0.5, "momentum": -0.05, "volatility": 0.10, "size": -0.2},
        },
        sector_map={"high_beta": "Technology", "hedge": "Financials"},
        portfolio_id="paper",
        factor_snapshot_id="factor-snap-1",
    )


def test_soft_penalty_reduces_beta_and_momentum_exposure(db_session: Session) -> None:
    service = FactorNeutralizationService(
        session=db_session,
        config=FactorNeutralizationConfig(
            enabled=True,
            mode=FactorNeutralizationMode.SOFT_PENALTY,
            constraints=[
                FactorNeutralizationConstraint(
                    factor_name="market_beta",
                    constraint_type=FactorConstraintType.TARGET_RANGE,
                    target_min=-0.05,
                    target_max=0.05,
                    penalty_weight=5.0,
                ),
                FactorNeutralizationConstraint(
                    factor_name="momentum",
                    constraint_type=FactorConstraintType.MAX_ABS_EXPOSURE,
                    max_abs_exposure=0.10,
                    penalty_weight=2.0,
                ),
            ],
        ),
    )

    result = service.neutralize(request=_request(), run_id="neutral-soft")

    assert result.status == FactorNeutralizationStatus.APPLIED
    post_beta = result.decomposition.post_neutralization["market_beta"]
    pre_beta = result.decomposition.pre_neutralization["market_beta"]
    assert post_beta is not None
    assert pre_beta is not None
    assert abs(post_beta) < abs(pre_beta)
    assert result.decomposition.exposure_reduction["momentum"] is not None


def test_hard_constraint_infeasible_falls_back(db_session: Session) -> None:
    req = FactorNeutralizationRequest(
        assets=["a", "b"],
        current_weights={"a": 0.5, "b": 0.5},
        factor_exposures={"a": {"market_beta": 1.0}, "b": {"market_beta": 2.0}},
    )
    service = FactorNeutralizationService(
        session=db_session,
        config=FactorNeutralizationConfig(
            enabled=True,
            mode=FactorNeutralizationMode.HARD_CONSTRAINT,
            constraints=[
                FactorNeutralizationConstraint(
                    factor_name="market_beta",
                    constraint_type=FactorConstraintType.TARGET_RANGE,
                    target_min=-0.05,
                    target_max=0.05,
                    hard_limit=True,
                    penalty_weight=10.0,
                )
            ],
        ),
    )

    result = service.neutralize(request=req, run_id="neutral-hard-infeasible")

    assert result.status == FactorNeutralizationStatus.FALLBACK_USED
    assert result.target_weights == result.original_weights
    assert result.infeasibility_reason is not None


def test_observe_only_preserves_weights_and_persists(db_session: Session) -> None:
    service = FactorNeutralizationService(
        session=db_session,
        config=FactorNeutralizationConfig(enabled=True, mode=FactorNeutralizationMode.OBSERVE_ONLY),
    )
    result = service.neutralize(request=_request(), run_id="neutral-observe")

    assert result.status == FactorNeutralizationStatus.OBSERVED
    assert result.target_weights == result.original_weights

    row = FactorNeutralizationRepository(db_session).get_by_run_id("neutral-observe")
    assert row is not None
    assert row.factor_snapshot_id == "factor-snap-1"
    assert (
        row.pre_exposures["market_beta"] == result.decomposition.pre_neutralization["market_beta"]
    )


def test_build_optimizer_factor_constraints_payload(db_session: Session) -> None:
    service = FactorNeutralizationService(
        session=db_session,
        config=FactorNeutralizationConfig(enabled=True, mode=FactorNeutralizationMode.SOFT_PENALTY),
    )
    payload = service.build_optimizer_factor_constraints()

    assert payload["enabled"] is True
    assert payload["mode"] == "soft_penalty"
    assert payload["constraints"] != []

    constraints = OptimizerConstraints(factor_neutralization=payload)
    assert constraints.factor_neutralization["enabled"] is True
