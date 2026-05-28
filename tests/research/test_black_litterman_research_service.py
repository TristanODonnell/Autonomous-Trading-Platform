from __future__ import annotations

import math

import numpy as np
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.research.black_litterman.black_litterman_research_service import (
    BlackLittermanInput,
    BlackLittermanResearchService,
    BlackLittermanValidationError,
    ResearchOnlyViolationError,
)
from autonomous_trading_platform.storage.sor.repositories.core.black_litterman_research_repository import (
    BlackLittermanResearchRepository,
)


def _config(**overrides) -> BlackLittermanInput:
    payload = {
        "universe_id": "test_universe",
        "universe": ["MomentumStrategy", "MeanReversionStrategy", "LowVolStrategy"],
        "covariance_matrix": {
            "MomentumStrategy": {
                "MomentumStrategy": 0.04,
                "MeanReversionStrategy": 0.01,
                "LowVolStrategy": 0.008,
            },
            "MeanReversionStrategy": {
                "MomentumStrategy": 0.01,
                "MeanReversionStrategy": 0.09,
                "LowVolStrategy": 0.006,
            },
            "LowVolStrategy": {
                "MomentumStrategy": 0.008,
                "MeanReversionStrategy": 0.006,
                "LowVolStrategy": 0.025,
            },
        },
        "covariance_snapshot_id": "cov_test",
        "benchmark_weights": {
            "MomentumStrategy": 0.4,
            "MeanReversionStrategy": 0.3,
            "LowVolStrategy": 0.3,
        },
        "risk_aversion": 2.5,
        "tau": 0.05,
        "views": [
            {
                "type": "absolute",
                "target": "MomentumStrategy",
                "expected_return": 0.08,
                "confidence": 0.7,
            }
        ],
        "persist_artifact": True,
    }
    payload.update(overrides)
    return BlackLittermanInput(**payload)


def test_implied_prior_return_calculation() -> None:
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    benchmark = np.array([0.6, 0.4])

    prior = BlackLittermanResearchService.compute_market_implied_prior(
        covariance_matrix=cov,
        benchmark_weights=benchmark,
        risk_aversion=2.5,
    )

    np.testing.assert_allclose(prior, 2.5 * cov @ benchmark)


def test_absolute_and_relative_views_generate_posterior_and_weights(db_session: Session) -> None:
    config = _config(
        views=[
            {
                "type": "absolute",
                "target": "MomentumStrategy",
                "expected_return": 0.08,
                "confidence": 0.7,
            },
            {
                "type": "relative",
                "long": "LowVolStrategy",
                "short": "MeanReversionStrategy",
                "expected_outperformance": 0.03,
                "confidence": 0.6,
            },
        ],
        constraints={"long_only": True, "max_weight": 0.7},
    )

    artifact = BlackLittermanResearchService(session=db_session).run(config)

    assert artifact.black_litterman_run_id.startswith("bl_")
    assert set(artifact.prior_returns) == set(config.universe)
    assert set(artifact.posterior_returns) == set(config.universe)
    assert "unconstrained" in artifact.proposed_weights
    assert "constrained" in artifact.proposed_weights
    assert math.isclose(sum(artifact.proposed_weights["constrained"].values()), 1.0, abs_tol=1e-6)
    assert artifact.diagnostics["view_count"] == 2

    persisted = BlackLittermanResearchRepository(db_session).get_by_run_id(
        artifact.black_litterman_run_id
    )
    assert persisted is not None
    assert persisted.artifact_hash == artifact.artifact_hash


def test_confidence_constructs_positive_omega(db_session: Session) -> None:
    artifact = BlackLittermanResearchService(session=db_session).run(
        _config(persist_artifact=False)
    )

    assert artifact.confidences[0][0] > 0.0
    assert artifact.diagnostics["omega_diagonal"][0] == round(artifact.confidences[0][0], 12)


def test_explicit_view_matrix_is_supported(db_session: Session) -> None:
    config = _config(
        views=[],
        view_matrix=[[1.0, -1.0, 0.0]],
        view_vector=[0.02],
        confidence_matrix=[[0.001]],
        view_metadata=[{"type": "relative", "long": "MomentumStrategy"}],
        persist_artifact=False,
    )

    artifact = BlackLittermanResearchService(session=db_session).run(config)

    assert artifact.views == [{"type": "relative", "long": "MomentumStrategy"}]
    assert (
        artifact.posterior_returns["MomentumStrategy"] != artifact.prior_returns["MomentumStrategy"]
    )


def test_dimension_mismatch_rejected(db_session: Session) -> None:
    config = _config(view_matrix=[[1.0, 0.0]], view_vector=[0.01], confidence_matrix=[[0.01]])

    with pytest.raises(BlackLittermanValidationError, match="view matrix dimensions"):
        BlackLittermanResearchService(session=db_session).run(config)


def test_invalid_view_asset_rejected(db_session: Session) -> None:
    config = _config(
        views=[
            {
                "type": "absolute",
                "target": "MissingStrategy",
                "expected_return": 0.1,
                "confidence": 0.5,
            }
        ],
        persist_artifact=False,
    )

    with pytest.raises(BlackLittermanValidationError, match="not in universe"):
        BlackLittermanResearchService(session=db_session).run(config)


def test_deterministic_hashing_for_same_inputs(db_session: Session) -> None:
    service = BlackLittermanResearchService(session=db_session)
    first = service.run(_config(persist_artifact=False))
    second = service.run(_config(persist_artifact=False))

    assert first.input_hash == second.input_hash
    assert first.views_hash == second.views_hash
    assert first.output_hash == second.output_hash
    assert first.artifact_hash == second.artifact_hash
    assert first.black_litterman_run_id != second.black_litterman_run_id


def test_research_only_guard_blocks_runtime_context(db_session: Session) -> None:
    with pytest.raises(ResearchOnlyViolationError):
        BlackLittermanResearchService(session=db_session).run(
            _config(run_context="paper_trading", persist_artifact=False)
        )


def test_infeasible_optimizer_constraints_rejected(db_session: Session) -> None:
    config = _config(constraints={"long_only": True, "max_weight": 0.2}, persist_artifact=False)

    with pytest.raises(BlackLittermanValidationError, match="infeasible optimizer constraints"):
        BlackLittermanResearchService(session=db_session).run(config)
