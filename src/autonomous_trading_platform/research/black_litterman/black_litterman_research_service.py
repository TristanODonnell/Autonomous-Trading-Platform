from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.mean_variance_optimizer import (
    MeanVarianceConfig,
    MeanVarianceOptimizer,
)
from autonomous_trading_platform.contracts.runtime.optimizer import (
    OptimizationObjective,
    OptimizerConstraints,
    SolverStatus,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.black_litterman_research_runs import (
    BlackLittermanResearchRunRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.black_litterman_research_repository import (
    BlackLittermanResearchRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)

logger = get_logger(__name__)

BLACK_LITTERMAN_RESEARCH_STARTED = "BLACK_LITTERMAN_RESEARCH_STARTED"
BLACK_LITTERMAN_PRIOR_COMPUTED = "BLACK_LITTERMAN_PRIOR_COMPUTED"
BLACK_LITTERMAN_POSTERIOR_COMPUTED = "BLACK_LITTERMAN_POSTERIOR_COMPUTED"
BLACK_LITTERMAN_RESEARCH_COMPLETED = "BLACK_LITTERMAN_RESEARCH_COMPLETED"
BLACK_LITTERMAN_RESEARCH_FAILED = "BLACK_LITTERMAN_RESEARCH_FAILED"

_ALLOWED_CONTEXTS = {
    "research_cli",
    "experiment_pipeline",
    "offline_simulation",
    "backtest",
    "dry_run_allocation_analysis",
}
_BLOCKED_CONTEXTS = {
    "live_trading",
    "paper_trading",
    "production_rebalance",
    "automatic_portfolio_construction",
    "runtime_allocation",
}


class BlackLittermanValidationError(ValueError):
    """Raised when a research run config is invalid."""


class ResearchOnlyViolationError(PermissionError):
    """Raised when Black-Litterman is invoked from a runtime allocation context."""


class BlackLittermanViewType(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class BlackLittermanView(BaseModel):
    type: BlackLittermanViewType
    target: str | None = None
    long: str | None = None
    short: str | None = None
    expected_return: float | None = None
    expected_outperformance: float | None = None
    confidence: float = Field(gt=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_view(self) -> BlackLittermanView:
        if self.type == BlackLittermanViewType.ABSOLUTE:
            if not self.target:
                raise ValueError("absolute view requires target")
            if self.expected_return is None:
                raise ValueError("absolute view requires expected_return")
        if self.type == BlackLittermanViewType.RELATIVE:
            if not self.long or not self.short:
                raise ValueError("relative view requires long and short")
            if self.long == self.short:
                raise ValueError("relative view long and short must differ")
            if self.expected_outperformance is None:
                raise ValueError("relative view requires expected_outperformance")
        return self


class BlackLittermanInput(BaseModel):
    universe_id: str | None = None
    universe: list[str]
    covariance_matrix: dict[str, dict[str, float]] | None = None
    covariance_snapshot_id: str | None = None
    benchmark_weights: dict[str, float]
    risk_aversion: float = Field(gt=0.0)
    tau: float = Field(gt=0.0)
    views: list[BlackLittermanView] = Field(default_factory=list)
    view_matrix: list[list[float]] | None = None
    view_vector: list[float] | None = None
    confidence_matrix: list[list[float]] | None = None
    view_metadata: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] | None = None
    current_weights: dict[str, float] | None = None
    run_context: str = "research_cli"
    run_id: str | None = None
    persist_artifact: bool = True

    @model_validator(mode="after")
    def validate_views_or_matrices(self) -> BlackLittermanInput:
        matrix_fields = [self.view_matrix, self.view_vector, self.confidence_matrix]
        if any(item is not None for item in matrix_fields) and not all(
            item is not None for item in matrix_fields
        ):
            raise ValueError(
                "view_matrix, view_vector, and confidence_matrix must be supplied together"
            )
        if not self.views and self.view_matrix is None:
            raise ValueError("at least one semantic view or explicit view matrix is required")
        return self


class BlackLittermanArtifact(BaseModel):
    black_litterman_run_id: str
    universe_id: str | None
    universe: list[str]
    covariance_snapshot_id: str | None
    covariance_snapshot_hash: str
    benchmark_weights: dict[str, float]
    tau: float
    risk_aversion: float
    views: list[dict[str, Any]]
    confidences: list[list[float]]
    prior_returns: dict[str, float]
    posterior_returns: dict[str, float]
    posterior_covariance: dict[str, dict[str, float]] | None
    proposed_weights: dict[str, Any]
    constraints_used: dict[str, Any]
    diagnostics: dict[str, Any]
    input_hash: str
    views_hash: str
    output_hash: str
    artifact_hash: str
    generated_at: datetime


class BlackLittermanResearchService:
    """Research-only Black-Litterman posterior return and allocation prototype."""

    def __init__(
        self,
        *,
        session: Session,
        repository: BlackLittermanResearchRepository | None = None,
        covariance_repository: CorrelationSnapshotRepository | None = None,
        optimizer: MeanVarianceOptimizer | None = None,
    ) -> None:
        self._session = session
        self._repo = repository or BlackLittermanResearchRepository(session)
        self._cov_repo = covariance_repository or CorrelationSnapshotRepository(session)
        self._optimizer = optimizer

    def run(self, config: BlackLittermanInput) -> BlackLittermanArtifact:
        self._enforce_research_only(config.run_context)
        run_id = config.run_id or f"bl_{uuid4().hex}"

        logger.info(
            "%s run_id=%s covariance_snapshot_id=%s universe_size=%d view_count=%d",
            BLACK_LITTERMAN_RESEARCH_STARTED,
            run_id,
            config.covariance_snapshot_id,
            len(config.universe),
            len(config.views),
        )

        try:
            universe = list(config.universe)
            cov, cov_snapshot_id, cov_snapshot_hash = self._resolve_covariance(config, universe)
            benchmark = self._validate_benchmark(config.benchmark_weights, universe)
            p_matrix, q_vector, omega, normalized_views = self._build_views(config, universe, cov)

            prior = self.compute_market_implied_prior(
                covariance_matrix=cov,
                benchmark_weights=benchmark,
                risk_aversion=config.risk_aversion,
            )
            logger.info(
                "%s run_id=%s covariance_snapshot_id=%s universe_size=%d",
                BLACK_LITTERMAN_PRIOR_COMPUTED,
                run_id,
                cov_snapshot_id,
                len(universe),
            )

            posterior, posterior_cov = self.compute_posterior_returns(
                prior_returns=prior,
                covariance_matrix=cov,
                tau=config.tau,
                view_matrix=p_matrix,
                view_vector=q_vector,
                confidence_matrix=omega,
            )
            self._validate_finite("posterior expected returns", posterior)
            logger.info(
                "%s run_id=%s covariance_snapshot_id=%s view_count=%d",
                BLACK_LITTERMAN_POSTERIOR_COMPUTED,
                run_id,
                cov_snapshot_id,
                p_matrix.shape[0],
            )

            weights, optimizer_status = self._build_allocation_proposal(
                run_id=run_id,
                universe=universe,
                posterior_returns=posterior,
                covariance_matrix=cov,
                covariance_snapshot_id=cov_snapshot_id,
                constraints=config.constraints,
                current_weights=config.current_weights,
                risk_aversion=config.risk_aversion,
                benchmark_weights=benchmark,
            )

            artifact = self._build_artifact(
                run_id=run_id,
                config=config,
                universe=universe,
                cov=cov,
                cov_snapshot_id=cov_snapshot_id,
                cov_snapshot_hash=cov_snapshot_hash,
                benchmark=benchmark,
                normalized_views=normalized_views,
                omega=omega,
                prior=prior,
                posterior=posterior,
                posterior_cov=posterior_cov,
                proposed_weights=weights,
                optimizer_status=optimizer_status,
            )
            if config.persist_artifact:
                self._persist(artifact, optimizer_status=optimizer_status)

            logger.info(
                "%s run_id=%s covariance_snapshot_id=%s universe_size=%d view_count=%d "
                "optimizer_status=%s artifact_hash=%s",
                BLACK_LITTERMAN_RESEARCH_COMPLETED,
                run_id,
                cov_snapshot_id,
                len(universe),
                p_matrix.shape[0],
                optimizer_status,
                artifact.artifact_hash,
            )
            return artifact
        except Exception as exc:
            logger.exception(
                "%s run_id=%s covariance_snapshot_id=%s error=%s",
                BLACK_LITTERMAN_RESEARCH_FAILED,
                run_id,
                config.covariance_snapshot_id,
                exc,
            )
            raise

    @staticmethod
    def compute_market_implied_prior(
        *,
        covariance_matrix: np.ndarray,
        benchmark_weights: np.ndarray,
        risk_aversion: float,
    ) -> np.ndarray:
        return risk_aversion * covariance_matrix @ benchmark_weights

    @staticmethod
    def compute_posterior_returns(
        *,
        prior_returns: np.ndarray,
        covariance_matrix: np.ndarray,
        tau: float,
        view_matrix: np.ndarray,
        view_vector: np.ndarray,
        confidence_matrix: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        tau_sigma = tau * covariance_matrix
        inv_tau_sigma = np.linalg.pinv(tau_sigma)
        inv_omega = np.linalg.pinv(confidence_matrix)
        middle = inv_tau_sigma + view_matrix.T @ inv_omega @ view_matrix
        rhs = inv_tau_sigma @ prior_returns + view_matrix.T @ inv_omega @ view_vector
        posterior_cov = np.linalg.pinv(middle)
        posterior = posterior_cov @ rhs
        return posterior, posterior_cov

    @staticmethod
    def stable_hash(payload: Any) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _enforce_research_only(run_context: str) -> None:
        if run_context in _BLOCKED_CONTEXTS or run_context not in _ALLOWED_CONTEXTS:
            raise ResearchOnlyViolationError(
                f"Black-Litterman is research-only and cannot run in context {run_context!r}"
            )

    def _resolve_covariance(
        self, config: BlackLittermanInput, universe: list[str]
    ) -> tuple[np.ndarray, str | None, str]:
        snapshot_id = config.covariance_snapshot_id
        matrix = config.covariance_matrix
        snapshot_hash: str | None = None

        if matrix is None:
            if snapshot_id is None:
                raise BlackLittermanValidationError(
                    "covariance_matrix or covariance_snapshot_id is required"
                )
            snap = self._cov_repo.get_covariance_by_snapshot_id(snapshot_id)
            if snap is None:
                raise BlackLittermanValidationError(
                    f"covariance snapshot {snapshot_id!r} was not found"
                )
            matrix = snap.covariance_matrix
            snapshot_hash = snap.matrix_hash

        try:
            cov = np.array([[matrix[a][b] for b in universe] for a in universe], dtype=float)
        except (KeyError, TypeError) as exc:
            raise BlackLittermanValidationError(
                f"covariance matrix does not cover universe ordering: {exc}"
            ) from exc

        if cov.shape != (len(universe), len(universe)):
            raise BlackLittermanValidationError("covariance dimensions do not match universe")
        if not np.all(np.isfinite(cov)):
            raise BlackLittermanValidationError("covariance matrix contains NaN or Inf")
        if not np.allclose(cov, cov.T, atol=1e-8):
            raise BlackLittermanValidationError("covariance matrix must be symmetric")

        matrix_payload = {
            a: {b: float(cov[i, j]) for j, b in enumerate(universe)} for i, a in enumerate(universe)
        }
        return cov, snapshot_id, snapshot_hash or self.stable_hash(matrix_payload)

    @staticmethod
    def _validate_benchmark(weights: dict[str, float], universe: list[str]) -> np.ndarray:
        missing = [asset for asset in universe if asset not in weights]
        extra = sorted(set(weights) - set(universe))
        if missing or extra:
            raise BlackLittermanValidationError(
                f"benchmark weights must match universe; missing={missing} extra={extra}"
            )
        vec = np.array([weights[asset] for asset in universe], dtype=float)
        if not np.all(np.isfinite(vec)):
            raise BlackLittermanValidationError("benchmark weights contain NaN or Inf")
        if np.any(vec < -1e-12):
            raise BlackLittermanValidationError("benchmark weights must be non-negative")
        if not math.isclose(float(vec.sum()), 1.0, rel_tol=1e-8, abs_tol=1e-8):
            raise BlackLittermanValidationError("benchmark weights must sum to 1.0")
        return vec

    def _build_views(
        self, config: BlackLittermanInput, universe: list[str], cov: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
        if config.view_matrix is not None:
            p_matrix = np.array(config.view_matrix, dtype=float)
            q_vector = np.array(config.view_vector, dtype=float)
            omega = np.array(config.confidence_matrix, dtype=float)
            normalized_views = list(config.view_metadata)
        else:
            p_rows: list[list[float]] = []
            q_values: list[float] = []
            normalized_views = []
            asset_index = {asset: idx for idx, asset in enumerate(universe)}

            for view in config.views:
                row = [0.0] * len(universe)
                if view.type == BlackLittermanViewType.ABSOLUTE:
                    if view.target not in asset_index:
                        raise BlackLittermanValidationError(
                            f"absolute view target {view.target!r} is not in universe"
                        )
                    row[asset_index[str(view.target)]] = 1.0
                    q_values.append(float(view.expected_return or 0.0))
                else:
                    if view.long not in asset_index or view.short not in asset_index:
                        raise BlackLittermanValidationError(
                            f"relative view assets {view.long!r}/{view.short!r} must be in universe"
                        )
                    row[asset_index[str(view.long)]] = 1.0
                    row[asset_index[str(view.short)]] = -1.0
                    q_values.append(float(view.expected_outperformance or 0.0))
                p_rows.append(row)
                normalized_views.append(view.model_dump(mode="json"))

            p_matrix = np.array(p_rows, dtype=float)
            q_vector = np.array(q_values, dtype=float)
            omega = self._omega_from_confidence(p_matrix, cov, config.tau, config.views)

        if p_matrix.ndim != 2 or p_matrix.shape[1] != len(universe):
            raise BlackLittermanValidationError("view matrix dimensions do not match universe")
        if q_vector.shape != (p_matrix.shape[0],):
            raise BlackLittermanValidationError("view vector dimensions do not match view matrix")
        if omega.shape != (p_matrix.shape[0], p_matrix.shape[0]):
            raise BlackLittermanValidationError("confidence matrix dimensions do not match views")
        if not np.all(np.isfinite(p_matrix)) or not np.all(np.isfinite(q_vector)):
            raise BlackLittermanValidationError("views contain NaN or Inf")
        if not np.all(np.isfinite(omega)):
            raise BlackLittermanValidationError("confidence matrix contains NaN or Inf")
        if np.any(np.diag(omega) <= 0):
            raise BlackLittermanValidationError("confidence matrix diagonal must be positive")
        return p_matrix, q_vector, omega, normalized_views

    @staticmethod
    def _omega_from_confidence(
        p_matrix: np.ndarray,
        cov: np.ndarray,
        tau: float,
        views: list[BlackLittermanView],
    ) -> np.ndarray:
        variances: list[float] = []
        scaled_cov = tau * cov
        for idx, view in enumerate(views):
            view_variance = float(p_matrix[idx] @ scaled_cov @ p_matrix[idx].T)
            confidence = float(view.confidence)
            variance = max(view_variance * ((1.0 - confidence) / confidence), 1e-12)
            variances.append(variance)
        return np.diag(variances)

    def _build_allocation_proposal(
        self,
        *,
        run_id: str,
        universe: list[str],
        posterior_returns: np.ndarray,
        covariance_matrix: np.ndarray,
        covariance_snapshot_id: str | None,
        constraints: dict[str, Any] | None,
        current_weights: dict[str, float] | None,
        risk_aversion: float,
        benchmark_weights: np.ndarray,
    ) -> tuple[dict[str, Any], str | None]:
        unconstrained = self._unconstrained_weights(
            posterior_returns=posterior_returns,
            covariance_matrix=covariance_matrix,
            risk_aversion=risk_aversion,
        )
        benchmark_dict = {
            asset: round(float(benchmark_weights[idx]), 12) for idx, asset in enumerate(universe)
        }
        proposed: dict[str, Any] = {
            "unconstrained": {
                asset: round(float(unconstrained[idx]), 12) for idx, asset in enumerate(universe)
            },
            "benchmark": benchmark_dict,
            "benchmark_delta": {
                asset: round(float(unconstrained[idx] - benchmark_weights[idx]), 12)
                for idx, asset in enumerate(universe)
            },
        }

        if current_weights:
            proposed["current_allocation_delta"] = {
                asset: round(float(unconstrained[idx] - current_weights.get(asset, 0.0)), 12)
                for idx, asset in enumerate(universe)
            }

        optimizer_status = None
        if constraints:
            optimizer_constraints = self._constraints_from_config(constraints)
            optimizer = self._optimizer or MeanVarianceOptimizer(
                session=self._session,
                config=MeanVarianceConfig(dry_run=True, risk_aversion=risk_aversion),
            )
            cov_dict = {
                a: {b: float(covariance_matrix[i, j]) for j, b in enumerate(universe)}
                for i, a in enumerate(universe)
            }
            opt_result = optimizer.optimize(
                assets=universe,
                expected_returns={
                    asset: float(posterior_returns[idx]) for idx, asset in enumerate(universe)
                },
                current_weights=current_weights,
                objective=OptimizationObjective.MAXIMUM_UTILITY,
                constraints=optimizer_constraints,
                expected_return_source="black_litterman_posterior_research",
                run_id=f"{run_id}_mvo",
                covariance_matrix=cov_dict,
                covariance_snapshot_id=covariance_snapshot_id,
            )
            optimizer_status = str(opt_result.solver_status)
            if opt_result.solver_status in {
                SolverStatus.INFEASIBLE,
                SolverStatus.FAILED,
                SolverStatus.FALLBACK_USED,
            }:
                raise BlackLittermanValidationError(
                    f"infeasible optimizer constraints: {opt_result.infeasibility_reason}"
                )
            proposed["constrained"] = opt_result.target_weights
            proposed["constrained_benchmark_delta"] = {
                asset: round(opt_result.target_weights.get(asset, 0.0) - benchmark_dict[asset], 12)
                for asset in universe
            }
            proposed["optimizer"] = opt_result.model_dump(mode="json")

        return proposed, optimizer_status

    @staticmethod
    def _unconstrained_weights(
        *, posterior_returns: np.ndarray, covariance_matrix: np.ndarray, risk_aversion: float
    ) -> np.ndarray:
        inv_cov = np.linalg.pinv(covariance_matrix)
        ones = np.ones(len(posterior_returns))
        a = float(ones @ inv_cov @ posterior_returns)
        b = float(ones @ inv_cov @ ones)
        if abs(b) < 1e-12:
            raise BlackLittermanValidationError("covariance matrix is singular for allocation")
        lagrange = (a - risk_aversion) / b
        return (inv_cov @ (posterior_returns - lagrange * ones)) / risk_aversion

    @staticmethod
    def _constraints_from_config(raw: dict[str, Any]) -> OptimizerConstraints:
        payload = dict(raw)
        if "max_weight" in payload and "global_max_weight" not in payload:
            payload["global_max_weight"] = payload.pop("max_weight")
        if "min_weight" in payload and "global_min_weight" not in payload:
            payload["global_min_weight"] = payload.pop("min_weight")
        return OptimizerConstraints(**payload)

    @staticmethod
    def _validate_finite(label: str, values: np.ndarray) -> None:
        if not np.all(np.isfinite(values)):
            raise BlackLittermanValidationError(f"{label} contain NaN or Inf")

    def _build_artifact(
        self,
        *,
        run_id: str,
        config: BlackLittermanInput,
        universe: list[str],
        cov: np.ndarray,
        cov_snapshot_id: str | None,
        cov_snapshot_hash: str,
        benchmark: np.ndarray,
        normalized_views: list[dict[str, Any]],
        omega: np.ndarray,
        prior: np.ndarray,
        posterior: np.ndarray,
        posterior_cov: np.ndarray,
        proposed_weights: dict[str, Any],
        optimizer_status: str | None,
    ) -> BlackLittermanArtifact:
        prior_dict = {asset: round(float(prior[idx]), 12) for idx, asset in enumerate(universe)}
        posterior_dict = {
            asset: round(float(posterior[idx]), 12) for idx, asset in enumerate(universe)
        }
        posterior_cov_dict = {
            a: {b: round(float(posterior_cov[i, j]), 12) for j, b in enumerate(universe)}
            for i, a in enumerate(universe)
        }
        benchmark_dict = {
            asset: round(float(benchmark[idx]), 12) for idx, asset in enumerate(universe)
        }
        cov_payload = {
            a: {b: float(cov[i, j]) for j, b in enumerate(universe)} for i, a in enumerate(universe)
        }
        input_payload = {
            "universe_id": config.universe_id,
            "universe": universe,
            "covariance_snapshot_id": cov_snapshot_id,
            "covariance_snapshot_hash": cov_snapshot_hash,
            "covariance_matrix_hash": self.stable_hash(cov_payload),
            "benchmark_weights": benchmark_dict,
            "risk_aversion": config.risk_aversion,
            "tau": config.tau,
            "views": normalized_views,
            "view_matrix": config.view_matrix,
            "view_vector": config.view_vector,
            "confidence_matrix": config.confidence_matrix,
            "constraints": config.constraints or {},
            "current_weights": config.current_weights or {},
            "run_context": config.run_context,
        }
        output_payload = {
            "prior_returns": prior_dict,
            "posterior_returns": posterior_dict,
            "posterior_covariance": posterior_cov_dict,
            "proposed_weights": proposed_weights,
            "optimizer_status": optimizer_status,
        }
        input_hash = self.stable_hash(input_payload)
        output_hash = self.stable_hash(output_payload)
        artifact_payload = {**input_payload, **output_payload, "input_hash": input_hash}
        artifact_hash = self.stable_hash(artifact_payload)
        diagnostics = {
            "view_count": len(normalized_views) if normalized_views else int(omega.shape[0]),
            "omega_diagonal": [round(float(x), 12) for x in np.diag(omega)],
            "view_prior_returns": [],
            "optimizer_status": optimizer_status,
            "research_only": True,
            "blocked_runtime_contexts": sorted(_BLOCKED_CONTEXTS),
        }
        return BlackLittermanArtifact(
            black_litterman_run_id=run_id,
            universe_id=config.universe_id,
            universe=universe,
            covariance_snapshot_id=cov_snapshot_id,
            covariance_snapshot_hash=cov_snapshot_hash,
            benchmark_weights=benchmark_dict,
            tau=config.tau,
            risk_aversion=config.risk_aversion,
            views=normalized_views,
            confidences=omega.tolist(),
            prior_returns=prior_dict,
            posterior_returns=posterior_dict,
            posterior_covariance=posterior_cov_dict,
            proposed_weights=proposed_weights,
            constraints_used=config.constraints or {},
            diagnostics=diagnostics,
            input_hash=input_hash,
            views_hash=self.stable_hash(normalized_views),
            output_hash=output_hash,
            artifact_hash=artifact_hash,
            generated_at=datetime.now(UTC),
        )

    def _persist(self, artifact: BlackLittermanArtifact, *, optimizer_status: str | None) -> None:
        row = BlackLittermanResearchRunRow(
            run_id=artifact.black_litterman_run_id,
            generated_at=artifact.generated_at,
            universe_id=artifact.universe_id,
            universe=artifact.universe,
            universe_size=len(artifact.universe),
            covariance_snapshot_id=artifact.covariance_snapshot_id,
            covariance_snapshot_hash=artifact.covariance_snapshot_hash,
            benchmark_weights=artifact.benchmark_weights,
            tau=artifact.tau,
            risk_aversion=artifact.risk_aversion,
            views=artifact.views,
            confidences=artifact.confidences,
            prior_returns=artifact.prior_returns,
            posterior_returns=artifact.posterior_returns,
            posterior_covariance=artifact.posterior_covariance,
            proposed_weights=artifact.proposed_weights,
            constraints_used=artifact.constraints_used,
            diagnostics=artifact.diagnostics,
            input_hash=artifact.input_hash,
            views_hash=artifact.views_hash,
            output_hash=artifact.output_hash,
            artifact_hash=artifact.artifact_hash,
            optimizer_status=optimizer_status,
            metadata_json={
                "artifact_type": "black_litterman_research_run",
                "artifact_version": 1,
                "research_only": True,
            },
        )
        self._repo.insert(row)
