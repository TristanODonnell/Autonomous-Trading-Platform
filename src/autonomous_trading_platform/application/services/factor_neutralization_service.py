from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import numpy as np
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.factor_neutralization import (
    FactorConstraintType,
    FactorExposureDecomposition,
    FactorNeutralizationConfigPayload,
    FactorNeutralizationConstraint,
    FactorNeutralizationMode,
    FactorNeutralizationRequest,
    FactorNeutralizationResult,
    FactorNeutralizationStatus,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_factor_constraint_binding_total,
    ratp_factor_exposure_reduction,
    ratp_factor_neutralization_duration_seconds,
    ratp_factor_neutralization_failures_total,
    ratp_portfolio_beta_exposure_post_neutralization,
)
from autonomous_trading_platform.storage.sor.models.factor_neutralization_runs import (
    FactorNeutralizationRunRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.factor_neutralization_repository import (
    FactorNeutralizationRepository,
)

logger = get_logger(__name__)

FACTOR_NEUTRALIZATION_APPLIED = "FACTOR_NEUTRALIZATION_APPLIED"
FACTOR_EXPOSURE_REDUCED = "FACTOR_EXPOSURE_REDUCED"
FACTOR_CONSTRAINT_BINDING = "FACTOR_CONSTRAINT_BINDING"
FACTOR_NEUTRALIZATION_INFEASIBLE = "FACTOR_NEUTRALIZATION_INFEASIBLE"
FACTOR_DRIFT_DETECTED = "FACTOR_DRIFT_DETECTED"

_NEUTRAL_FACTORS = ("market_beta", "momentum", "volatility", "sector", "size")
_EPS = 1e-9


@dataclass(frozen=True)
class FactorNeutralizationConfig:
    enabled: bool = False
    mode: FactorNeutralizationMode = FactorNeutralizationMode.OBSERVE_ONLY
    constraints: list[FactorNeutralizationConstraint] = field(default_factory=list)
    max_iterations: int = 500
    convergence_tolerance: float = 1e-8
    max_turnover: float | None = None
    fallback_mode: str = "previous_weights"

    def resolved_constraints(self) -> list[FactorNeutralizationConstraint]:
        if self.constraints:
            return list(self.constraints)
        return [
            FactorNeutralizationConstraint(
                factor_name="market_beta",
                constraint_type=FactorConstraintType.TARGET_RANGE,
                target_min=-0.05,
                target_max=0.05,
                penalty_weight=2.0,
            ),
            FactorNeutralizationConstraint(
                factor_name="momentum",
                constraint_type=FactorConstraintType.MAX_ABS_EXPOSURE,
                max_abs_exposure=0.10,
                penalty_weight=1.0,
            ),
            FactorNeutralizationConstraint(
                factor_name="volatility",
                constraint_type=FactorConstraintType.MAX_ABS_EXPOSURE,
                max_abs_exposure=0.50,
                penalty_weight=1.0,
            ),
            FactorNeutralizationConstraint(
                factor_name="size",
                constraint_type=FactorConstraintType.TARGET_RANGE,
                target_min=-0.10,
                target_max=0.10,
                penalty_weight=0.5,
            ),
        ]

    def to_payload(self) -> FactorNeutralizationConfigPayload:
        return FactorNeutralizationConfigPayload(
            enabled=self.enabled,
            mode=self.mode,
            constraints=self.resolved_constraints(),
            max_iterations=self.max_iterations,
            convergence_tolerance=self.convergence_tolerance,
            max_turnover=self.max_turnover,
        )


class FactorNeutralizationService:
    """Advisory factor-neutralization controls for portfolio construction.

    The service is optional and does not mutate existing allocation state. In
    observe-only mode it records diagnostics. In soft-penalty mode it recommends
    lower-factor-exposure weights. In hard-constraint mode it requires configured
    ranges/caps to be feasible or returns an auditable fallback.
    """

    def __init__(
        self,
        *,
        session: Session,
        config: FactorNeutralizationConfig | None = None,
        repo: FactorNeutralizationRepository | None = None,
    ) -> None:
        self._session = session
        self._config = config or FactorNeutralizationConfig()
        self._repo = repo or FactorNeutralizationRepository(session)

    def neutralize(
        self,
        *,
        request: FactorNeutralizationRequest,
        run_id: str | None = None,
    ) -> FactorNeutralizationResult:
        if run_id is None:
            run_id = str(uuid4())
        wall_start = perf_counter()
        generated_at = datetime.now(UTC)
        warnings: list[str] = []

        assets = list(request.assets)
        if not assets:
            return self._build_result(
                run_id=run_id,
                generated_at=generated_at,
                request=request,
                status=FactorNeutralizationStatus.INFEASIBLE,
                target_weights={},
                original_weights={},
                pre={},
                post={},
                binding=[],
                violations=[],
                fallback_mode=None,
                infeasibility_reason="no_assets_provided",
                duration=perf_counter() - wall_start,
                warnings=["No assets provided for factor neutralization"],
            )

        original_weights = _normalize_weights(
            {asset: request.current_weights.get(asset, 0.0) for asset in assets}
        )
        pre = self._compute_exposures(
            assets=assets,
            weights=original_weights,
            factor_exposures=request.factor_exposures,
            sector_map=request.sector_map,
        )

        if not self._config.enabled:
            result = self._build_result(
                run_id=run_id,
                generated_at=generated_at,
                request=request,
                status=FactorNeutralizationStatus.DISABLED,
                target_weights=original_weights,
                original_weights=original_weights,
                pre=pre,
                post=pre,
                binding=[],
                violations=self._violations(pre),
                fallback_mode="disabled",
                infeasibility_reason=None,
                duration=perf_counter() - wall_start,
                warnings=["Factor neutralization disabled; observe-only decomposition recorded"],
            )
            self._persist(result)
            return result

        if self._config.mode == FactorNeutralizationMode.OBSERVE_ONLY:
            result = self._build_result(
                run_id=run_id,
                generated_at=generated_at,
                request=request,
                status=FactorNeutralizationStatus.OBSERVED,
                target_weights=original_weights,
                original_weights=original_weights,
                pre=pre,
                post=pre,
                binding=[],
                violations=self._violations(pre),
                fallback_mode="observe_only",
                infeasibility_reason=None,
                duration=perf_counter() - wall_start,
                warnings=warnings,
            )
            self._persist(result)
            self._emit_events(result)
            self._record_metrics(result)
            return result

        matrix, factor_names, factor_warnings = self._factor_matrix(
            assets, request.factor_exposures
        )
        warnings.extend(factor_warnings)
        if matrix.size == 0:
            result = self._fallback(
                run_id=run_id,
                generated_at=generated_at,
                request=request,
                original_weights=original_weights,
                pre=pre,
                reason="missing_factor_exposure_data",
                duration=perf_counter() - wall_start,
                warnings=warnings,
            )
            self._persist(result)
            self._emit_events(result)
            self._record_metrics(result)
            return result

        try:
            if self._config.mode == FactorNeutralizationMode.HARD_CONSTRAINT:
                target = self._hard_neutralize(
                    assets=assets,
                    original_weights=original_weights,
                    matrix=matrix,
                    factor_names=factor_names,
                    sector_map=request.sector_map,
                )
            else:
                target = self._soft_neutralize(
                    assets=assets,
                    original_weights=original_weights,
                    matrix=matrix,
                    factor_names=factor_names,
                    sector_map=request.sector_map,
                )
        except ValueError as exc:
            result = self._fallback(
                run_id=run_id,
                generated_at=generated_at,
                request=request,
                original_weights=original_weights,
                pre=pre,
                reason=str(exc),
                duration=perf_counter() - wall_start,
                warnings=warnings,
            )
            self._persist(result)
            self._emit_events(result)
            self._record_metrics(result)
            return result

        post = self._compute_exposures(
            assets=assets,
            weights=target,
            factor_exposures=request.factor_exposures,
            sector_map=request.sector_map,
        )
        violations = self._violations(post)
        binding = self._binding_constraints(post)
        status = (
            FactorNeutralizationStatus.INFEASIBLE
            if self._config.mode == FactorNeutralizationMode.HARD_CONSTRAINT and violations
            else FactorNeutralizationStatus.APPLIED
        )
        result = self._build_result(
            run_id=run_id,
            generated_at=generated_at,
            request=request,
            status=status,
            target_weights=target,
            original_weights=original_weights,
            pre=pre,
            post=post,
            binding=binding,
            violations=violations,
            fallback_mode=None,
            infeasibility_reason=(
                "hard_constraints_not_satisfied"
                if status == FactorNeutralizationStatus.INFEASIBLE
                else None
            ),
            duration=perf_counter() - wall_start,
            warnings=warnings,
        )
        self._persist(result)
        self._emit_events(result)
        self._record_metrics(result)
        return result

    def build_optimizer_factor_constraints(self) -> dict[str, Any]:
        """Return optimizer-compatible neutralization metadata.

        Existing MVO flows can pass this payload through metadata without changing
        behavior when neutralization is disabled. Future solvers can translate it
        into native linear factor constraints.
        """
        return cast(dict[str, Any], self._config.to_payload().model_dump(mode="json"))

    def _soft_neutralize(
        self,
        *,
        assets: list[str],
        original_weights: dict[str, float],
        matrix: np.ndarray,
        factor_names: list[str],
        sector_map: dict[str, str],
    ) -> dict[str, float]:
        w0 = np.array([original_weights[a] for a in assets], dtype=float)
        w = w0.copy()
        penalty = self._penalty_vector(factor_names)
        step = 0.05
        for _ in range(self._config.max_iterations):
            prev = w.copy()
            exposure = matrix @ w
            grad = (w - w0) + matrix.T @ (2.0 * penalty * exposure)
            w = _project_simplex(w - step * grad)
            w = self._apply_sector_caps(assets, w, sector_map)
            if self._config.max_turnover is not None:
                w = self._limit_turnover(w=w, w0=w0, max_turnover=self._config.max_turnover)
            if float(np.max(np.abs(w - prev))) < self._config.convergence_tolerance:
                break
        return {asset: round(float(w[i]), 10) for i, asset in enumerate(assets)}

    def _hard_neutralize(
        self,
        *,
        assets: list[str],
        original_weights: dict[str, float],
        matrix: np.ndarray,
        factor_names: list[str],
        sector_map: dict[str, str],
    ) -> dict[str, float]:
        target = self._soft_neutralize(
            assets=assets,
            original_weights=original_weights,
            matrix=matrix,
            factor_names=factor_names,
            sector_map=sector_map,
        )
        post = self._compute_exposures(
            assets=assets,
            weights=target,
            factor_exposures={
                asset: {factor_names[j]: float(matrix[j, i]) for j in range(len(factor_names))}
                for i, asset in enumerate(assets)
            },
            sector_map=sector_map,
        )
        violations = self._violations(post)
        if violations:
            raise ValueError("factor_neutralization_constraints_infeasible")
        return target

    def _compute_exposures(
        self,
        *,
        assets: list[str],
        weights: dict[str, float],
        factor_exposures: dict[str, dict[str, float]],
        sector_map: dict[str, str],
    ) -> dict[str, float | None]:
        exposures: dict[str, float | None] = {}
        for factor in _NEUTRAL_FACTORS:
            if factor == "sector":
                sector_weights: dict[str, float] = {}
                for asset in assets:
                    sector = sector_map.get(asset, "unknown")
                    sector_weights[sector] = sector_weights.get(sector, 0.0) + abs(
                        weights.get(asset, 0.0)
                    )
                exposures[factor] = (
                    round(max(sector_weights.values()), 6) if sector_weights else None
                )
                continue
            total = 0.0
            found = False
            for asset in assets:
                value = factor_exposures.get(asset, {}).get(factor)
                if value is None or not math.isfinite(value):
                    continue
                total += weights.get(asset, 0.0) * value
                found = True
            exposures[factor] = round(total, 6) if found else None
        return exposures

    def _factor_matrix(
        self, assets: list[str], factor_exposures: dict[str, dict[str, float]]
    ) -> tuple[np.ndarray, list[str], list[str]]:
        factors = [
            c.factor_name for c in self._config.resolved_constraints() if c.factor_name != "sector"
        ]
        rows: list[list[float]] = []
        warnings: list[str] = []
        used: list[str] = []
        for factor in factors:
            row: list[float] = []
            missing = []
            for asset in assets:
                value = factor_exposures.get(asset, {}).get(factor)
                if value is None or not math.isfinite(value):
                    missing.append(asset)
                    row.append(0.0)
                else:
                    row.append(float(value))
            if len(missing) == len(assets):
                warnings.append(f"Missing all factor exposure data for {factor}")
                continue
            if missing:
                warnings.append(f"Missing {factor} exposures for assets: {missing}")
            rows.append(row)
            used.append(factor)
        if not rows:
            return np.array([], dtype=float), [], warnings
        return np.array(rows, dtype=float), used, warnings

    def _penalty_vector(self, factor_names: list[str]) -> np.ndarray:
        penalties = []
        constraints = {c.factor_name: c for c in self._config.resolved_constraints()}
        for factor in factor_names:
            default_constraint = FactorNeutralizationConstraint(
                factor_name=factor,
                constraint_type=FactorConstraintType.MAX_ABS_EXPOSURE,
            )
            penalties.append(max(constraints.get(factor, default_constraint).penalty_weight, 0.0))
        return np.array(penalties, dtype=float)

    def _apply_sector_caps(
        self, assets: list[str], weights: np.ndarray, sector_map: dict[str, str]
    ) -> np.ndarray:
        caps = {
            c.factor_name.removeprefix("sector:")
            if c.factor_name.startswith("sector:")
            else c.factor_name: c.target_max
            for c in self._config.resolved_constraints()
            if c.constraint_type == FactorConstraintType.SECTOR_CAP and c.target_max is not None
        }
        if not caps:
            return weights
        w = weights.copy()
        for sector, cap in caps.items():
            idx = [i for i, asset in enumerate(assets) if sector_map.get(asset) == sector]
            total = float(np.sum(w[idx])) if idx else 0.0
            if total > cap + _EPS and total > 0:
                excess = total - cap
                for i in idx:
                    w[i] = max(0.0, w[i] - excess * (w[i] / total))
                w = _project_simplex(w)
        return w

    @staticmethod
    def _limit_turnover(*, w: np.ndarray, w0: np.ndarray, max_turnover: float) -> np.ndarray:
        turnover = float(np.sum(np.abs(w - w0)))
        if turnover <= max_turnover or turnover <= _EPS:
            return w
        blend = max_turnover / turnover
        return _project_simplex(w0 + blend * (w - w0))

    def _violations(self, exposures: dict[str, float | None]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for constraint in self._config.resolved_constraints():
            value = exposures.get(
                "sector"
                if constraint.constraint_type == FactorConstraintType.SECTOR_CAP
                else constraint.factor_name
            )
            if value is None:
                continue
            violated = False
            limit: float | None = None
            if constraint.constraint_type == FactorConstraintType.TARGET_RANGE:
                low = constraint.target_min if constraint.target_min is not None else -math.inf
                high = constraint.target_max if constraint.target_max is not None else math.inf
                violated = value < low or value > high
                limit = high if value > high else low
            elif constraint.constraint_type == FactorConstraintType.MAX_ABS_EXPOSURE:
                limit = constraint.max_abs_exposure
                violated = limit is not None and abs(value) > limit
            elif constraint.constraint_type == FactorConstraintType.SECTOR_CAP:
                limit = constraint.target_max
                violated = limit is not None and value > limit
            if violated:
                violations.append(
                    {
                        "factor_name": constraint.factor_name,
                        "constraint_type": str(constraint.constraint_type),
                        "exposure": value,
                        "limit": limit,
                    }
                )
        return violations

    def _binding_constraints(self, exposures: dict[str, float | None]) -> list[str]:
        binding = []
        for constraint in self._config.resolved_constraints():
            value = exposures.get(
                "sector"
                if constraint.constraint_type == FactorConstraintType.SECTOR_CAP
                else constraint.factor_name
            )
            if value is None:
                continue
            if constraint.constraint_type == FactorConstraintType.TARGET_RANGE:
                if constraint.target_min is not None and abs(value - constraint.target_min) < 1e-3:
                    binding.append(f"{constraint.factor_name}:target_min")
                if constraint.target_max is not None and abs(value - constraint.target_max) < 1e-3:
                    binding.append(f"{constraint.factor_name}:target_max")
            elif constraint.constraint_type == FactorConstraintType.MAX_ABS_EXPOSURE:
                if (
                    constraint.max_abs_exposure is not None
                    and abs(abs(value) - constraint.max_abs_exposure) < 1e-3
                ):
                    binding.append(f"{constraint.factor_name}:max_abs")
            elif constraint.constraint_type == FactorConstraintType.SECTOR_CAP:
                if constraint.target_max is not None and abs(value - constraint.target_max) < 1e-3:
                    binding.append(f"{constraint.factor_name}:sector_cap")
        return binding

    def _build_result(
        self,
        *,
        run_id: str,
        generated_at: datetime,
        request: FactorNeutralizationRequest,
        status: FactorNeutralizationStatus,
        target_weights: dict[str, float],
        original_weights: dict[str, float],
        pre: dict[str, float | None],
        post: dict[str, float | None],
        binding: list[str],
        violations: list[dict[str, Any]],
        fallback_mode: str | None,
        infeasibility_reason: str | None,
        duration: float,
        warnings: list[str],
    ) -> FactorNeutralizationResult:
        reduction = {
            factor: _exposure_reduction(pre.get(factor), post.get(factor))
            for factor in sorted(set(pre) | set(post))
        }
        utilization = self._constraint_utilization(post)
        return FactorNeutralizationResult(
            run_id=run_id,
            generated_at=generated_at,
            status=status,
            mode=self._config.mode,
            portfolio_id=request.portfolio_id,
            factor_snapshot_id=request.factor_snapshot_id,
            covariance_snapshot_id=request.covariance_snapshot_id,
            optimization_run_id=request.optimization_run_id,
            target_weights=target_weights,
            original_weights=original_weights,
            decomposition=FactorExposureDecomposition(
                pre_neutralization=pre,
                post_neutralization=post,
                exposure_reduction=reduction,
                residual_exposure=post,
                constraint_utilization=utilization,
            ),
            binding_constraints=binding,
            constraint_violations=violations,
            fallback_mode=fallback_mode,
            infeasibility_reason=infeasibility_reason,
            duration_seconds=round(duration, 4),
            warnings=warnings,
            metadata={"optimizer_constraints": self.build_optimizer_factor_constraints()},
        )

    def _fallback(
        self,
        *,
        run_id: str,
        generated_at: datetime,
        request: FactorNeutralizationRequest,
        original_weights: dict[str, float],
        pre: dict[str, float | None],
        reason: str,
        duration: float,
        warnings: list[str],
    ) -> FactorNeutralizationResult:
        return self._build_result(
            run_id=run_id,
            generated_at=generated_at,
            request=request,
            status=FactorNeutralizationStatus.FALLBACK_USED,
            target_weights=original_weights,
            original_weights=original_weights,
            pre=pre,
            post=pre,
            binding=[],
            violations=self._violations(pre),
            fallback_mode=self._config.fallback_mode,
            infeasibility_reason=reason,
            duration=duration,
            warnings=warnings + [reason],
        )

    def _constraint_utilization(
        self, exposures: dict[str, float | None]
    ) -> dict[str, float | None]:
        utilization: dict[str, float | None] = {}
        for constraint in self._config.resolved_constraints():
            key = (
                "sector"
                if constraint.constraint_type == FactorConstraintType.SECTOR_CAP
                else constraint.factor_name
            )
            value = exposures.get(key)
            if value is None:
                utilization[constraint.factor_name] = None
                continue
            denom = (
                constraint.max_abs_exposure
                or constraint.target_max
                or abs(constraint.target_min or 0.0)
            )
            utilization[constraint.factor_name] = (
                round(abs(value) / denom, 6) if denom and denom > 0 else None
            )
        return utilization

    def _persist(self, result: FactorNeutralizationResult) -> None:
        self._repo.insert(
            FactorNeutralizationRunRow(
                run_id=result.run_id,
                generated_at=result.generated_at,
                status=str(result.status),
                mode=str(result.mode),
                portfolio_id=result.portfolio_id,
                factor_snapshot_id=result.factor_snapshot_id,
                covariance_snapshot_id=result.covariance_snapshot_id,
                optimization_run_id=result.optimization_run_id,
                config_json=self._config.to_payload().model_dump(mode="json"),
                original_weights=result.original_weights,
                target_weights=result.target_weights,
                pre_exposures=result.decomposition.pre_neutralization,
                post_exposures=result.decomposition.post_neutralization,
                exposure_reduction=result.decomposition.exposure_reduction,
                residual_exposure=result.decomposition.residual_exposure,
                constraint_utilization=result.decomposition.constraint_utilization,
                binding_constraints=result.binding_constraints,
                constraint_violations=result.constraint_violations,
                fallback_mode=result.fallback_mode,
                infeasibility_reason=result.infeasibility_reason,
                duration_seconds=result.duration_seconds,
                warnings=result.warnings,
                metadata_json=result.metadata,
            )
        )

    def _emit_events(self, result: FactorNeutralizationResult) -> None:
        if result.status in {
            FactorNeutralizationStatus.INFEASIBLE,
            FactorNeutralizationStatus.FALLBACK_USED,
        }:
            logger.warning(
                "%s run_id=%s status=%s reason=%s fallback=%s",
                FACTOR_NEUTRALIZATION_INFEASIBLE,
                result.run_id,
                result.status,
                result.infeasibility_reason,
                result.fallback_mode,
            )
            return
        logger.info(
            "%s run_id=%s mode=%s status=%s violations=%d",
            FACTOR_NEUTRALIZATION_APPLIED,
            result.run_id,
            result.mode,
            result.status,
            len(result.constraint_violations),
        )
        for factor, reduction in result.decomposition.exposure_reduction.items():
            if reduction is not None and reduction > 0:
                logger.info(
                    "%s run_id=%s factor=%s pre=%s post=%s reduction=%.6f",
                    FACTOR_EXPOSURE_REDUCED,
                    result.run_id,
                    factor,
                    result.decomposition.pre_neutralization.get(factor),
                    result.decomposition.post_neutralization.get(factor),
                    reduction,
                )
        for constraint in result.binding_constraints:
            logger.warning(
                "%s run_id=%s constraint=%s",
                FACTOR_CONSTRAINT_BINDING,
                result.run_id,
                constraint,
            )

    def _record_metrics(self, result: FactorNeutralizationResult) -> None:
        attrs = {"mode": str(result.mode), "status": str(result.status)}
        beta = result.decomposition.post_neutralization.get("market_beta")
        if beta is not None:
            ratp_portfolio_beta_exposure_post_neutralization.record(float(beta), attrs)
        for factor, reduction in result.decomposition.exposure_reduction.items():
            if reduction is not None:
                ratp_factor_exposure_reduction.record(float(reduction), {**attrs, "factor": factor})
        if result.binding_constraints:
            ratp_factor_constraint_binding_total.add(len(result.binding_constraints), attrs)
        if result.status in {
            FactorNeutralizationStatus.INFEASIBLE,
            FactorNeutralizationStatus.FALLBACK_USED,
        }:
            ratp_factor_neutralization_failures_total.add(1, attrs)
        ratp_factor_neutralization_duration_seconds.record(result.duration_seconds, attrs)

    def get_latest_run(
        self, *, portfolio_id: str | None = None, mode: FactorNeutralizationMode | None = None
    ) -> FactorNeutralizationRunRow | None:
        return self._repo.get_latest(
            portfolio_id=portfolio_id,
            mode=str(mode) if mode is not None else None,
        )

    @staticmethod
    def result_to_jsonable(result: FactorNeutralizationResult) -> dict[str, Any]:
        return dict(result.model_dump(mode="json"))


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {k: max(float(v), 0.0) for k, v in weights.items() if math.isfinite(float(v))}
    total = sum(cleaned.values())
    if total <= _EPS:
        equal = 1.0 / max(len(weights), 1)
        return {k: equal for k in weights}
    return {k: v / total for k, v in cleaned.items()}


def _project_simplex(values: np.ndarray) -> np.ndarray:
    v = np.maximum(values, 0.0)
    if float(np.sum(v)) <= _EPS:
        return np.ones(len(v)) / max(len(v), 1)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    rho_candidates = u * np.arange(1, len(u) + 1) > (cssv - 1)
    if not np.any(rho_candidates):
        return v / float(np.sum(v))
    rho = int(np.nonzero(rho_candidates)[0][-1])
    theta = (cssv[rho] - 1.0) / float(rho + 1)
    return np.maximum(v - theta, 0.0)


def _exposure_reduction(pre: float | None, post: float | None) -> float | None:
    if pre is None or post is None:
        return None
    return round(abs(pre) - abs(post), 6)
