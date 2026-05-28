from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.risk_budget import (
    AllocationMode,
    FallbackReason,
    RiskBudgetingRunResult,
    StrategyRiskContribution,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_diversification_ratio,
    ratp_portfolio_volatility_estimate,
    ratp_risk_budget_utilization,
    ratp_risk_parity_compute_duration_seconds,
    ratp_strategy_risk_contribution,
)
from autonomous_trading_platform.storage.sor.models.risk_budget_snapshots import (
    RiskBudgetSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_budget_snapshot_repository import (
    RiskBudgetSnapshotRepository,
)

logger = get_logger(__name__)

# Structured log event names
RISK_BUDGET_COMPUTED = "RISK_BUDGET_COMPUTED"
RISK_PARITY_ALLOCATION_COMPUTED = "RISK_PARITY_ALLOCATION_COMPUTED"
RISK_BUDGET_FALLBACK_USED = "RISK_BUDGET_FALLBACK_USED"
UNSTABLE_COVARIANCE_DETECTED = "UNSTABLE_COVARIANCE_DETECTED"

_ERC_MAX_ITERATIONS = 500
_ERC_CONVERGENCE_TOL = 1e-8
_MIN_WEIGHT = 1e-6
_MAX_CONDITION_NUMBER = 1e12
_ANNUALIZATION_FACTOR = 252
_MIN_STRATEGIES = 2


@dataclass(frozen=True)
class RiskBudgetConfig:
    """Controls how risk budgets and allocation modes are applied."""

    mode: AllocationMode = AllocationMode.EQUAL_RISK_CONTRIBUTION
    # Fixed risk budgets per strategy (must sum to 1.0 when provided).
    # If empty, equal budgets are assumed.
    risk_budgets: dict[str, float] = field(default_factory=dict)
    covariance_lookback_window: int = 60
    min_weight: float = _MIN_WEIGHT
    max_weight: float = 1.0
    annualization_factor: int = _ANNUALIZATION_FACTOR
    erc_max_iterations: int = _ERC_MAX_ITERATIONS
    erc_convergence_tol: float = _ERC_CONVERGENCE_TOL


@dataclass(frozen=True)
class _ErcResult:
    weights: np.ndarray
    iterations: int
    converged: bool
    warnings: list[str]


class RiskBudgetingService:
    """Computes and persists risk-budgeted allocation recommendations.

    Supported modes:
    - EQUAL_CAPITAL: uniform weights (baseline, no covariance required).
    - INVERSE_VOLATILITY: weights inversely proportional to realized volatility.
    - FIXED_RISK_BUDGETS: caller-supplied target risk fractions per strategy.
    - EQUAL_RISK_CONTRIBUTION: iterative ERC optimizer (requires covariance).

    Backward compatible: does NOT modify the QualityBasedReallocationService or
    existing AllocationOverrides.  Outputs are observability and advisory only.
    """

    def __init__(
        self,
        *,
        session: Session,
        config: RiskBudgetConfig | None = None,
        risk_budget_repo: RiskBudgetSnapshotRepository | None = None,
        correlation_repo: CorrelationSnapshotRepository | None = None,
    ) -> None:
        self._session = session
        self._config = config or RiskBudgetConfig()
        self._repo = risk_budget_repo or RiskBudgetSnapshotRepository(session)
        self._corr_repo = correlation_repo or CorrelationSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute(
        self,
        *,
        strategy_ids: list[str],
        run_id: str | None = None,
    ) -> RiskBudgetingRunResult:
        """Compute risk-budgeted allocation weights for `strategy_ids`.

        Args:
            strategy_ids: Ordered list of strategy identifiers to allocate across.
            run_id: Caller-supplied run ID for lineage tracing.

        Returns:
            RiskBudgetingRunResult with recommended weights and risk diagnostics.
        """
        if run_id is None:
            run_id = str(uuid4())

        wall_start = perf_counter()
        now = datetime.now(UTC)

        if len(strategy_ids) < _MIN_STRATEGIES:
            return self._empty_result(
                run_id=run_id,
                now=now,
                strategy_ids=strategy_ids,
                duration=perf_counter() - wall_start,
            )

        strategy_ids = list(strategy_ids)  # defensive copy

        # Normalise risk budgets
        budgets = self._resolve_budgets(strategy_ids)

        # Load covariance snapshot
        cov_matrix, cov_snapshot_id, fallback_reason = self._load_covariance(strategy_ids)

        # Load realized volatilities (used for fallback and diagnostics)
        vol_by_strategy = self._load_realized_volatilities(strategy_ids)

        warnings: list[str] = []
        fallback_used = False
        weights: np.ndarray
        iterations: int | None = None
        converged = False

        mode = self._config.mode
        if cov_matrix is None:
            # No covariance available — fall back based on mode
            if mode in (
                AllocationMode.EQUAL_RISK_CONTRIBUTION,
                AllocationMode.FIXED_RISK_BUDGETS,
            ):
                fallback_used = True
                if vol_by_strategy:
                    mode_used = AllocationMode.INVERSE_VOLATILITY
                    warnings.append(
                        f"Covariance unavailable ({fallback_reason}); "
                        "falling back to inverse-volatility weighting"
                    )
                else:
                    mode_used = AllocationMode.EQUAL_CAPITAL
                    warnings.append(
                        f"Covariance and volatility both unavailable ({fallback_reason}); "
                        "falling back to equal capital allocation"
                    )
            else:
                mode_used = mode

            weights = self._compute_fallback_weights(
                strategy_ids=strategy_ids,
                mode=mode_used,
                vol_by_strategy=vol_by_strategy,
                budgets=budgets,
            )
            converged = True
        else:
            mode_used = mode
            if mode == AllocationMode.EQUAL_RISK_CONTRIBUTION:
                erc = self._erc_weights(
                    strategies=strategy_ids,
                    cov_matrix=cov_matrix,
                    budgets=budgets,
                )
                weights = erc.weights
                iterations = erc.iterations
                converged = erc.converged
                warnings.extend(erc.warnings)
                if not converged:
                    warnings.append(
                        f"ERC did not converge in {self._config.erc_max_iterations} iterations"
                    )
            elif mode == AllocationMode.FIXED_RISK_BUDGETS:
                erc = self._erc_weights(
                    strategies=strategy_ids,
                    cov_matrix=cov_matrix,
                    budgets=budgets,
                )
                weights = erc.weights
                iterations = erc.iterations
                converged = erc.converged
                warnings.extend(erc.warnings)
            elif mode == AllocationMode.INVERSE_VOLATILITY:
                weights = self._inverse_vol_weights(
                    strategy_ids=strategy_ids, vol_by_strategy=vol_by_strategy
                )
                converged = True
            else:
                weights = self._equal_weights(len(strategy_ids))
                converged = True

        # Clamp weights to [min_weight, max_weight] and renormalize
        weights = self._clamp_and_normalize(weights)

        # Risk contributions
        if cov_matrix is not None:
            rc_list, port_vol = self._compute_risk_contributions(
                strategies=strategy_ids,
                weights=weights,
                cov_matrix=cov_matrix,
                budgets=budgets,
            )
        else:
            rc_list, port_vol = self._compute_vol_contributions(
                strategies=strategy_ids,
                weights=weights,
                vol_by_strategy=vol_by_strategy,
                budgets=budgets,
            )

        diversification_ratio = self._diversification_ratio(
            strategies=strategy_ids,
            weights=weights,
            vol_by_strategy=vol_by_strategy,
            port_vol=port_vol,
        )

        recommended = {sid: float(weights[i]) for i, sid in enumerate(strategy_ids)}
        hidden_conc = self._detect_hidden_concentration(rc_list)

        if hidden_conc:
            max_rc = max(rc.pct_of_portfolio_variance for rc in rc_list) if rc_list else 0.0
            warnings.append(
                f"Hidden volatility concentration: one strategy contributes "
                f"{max_rc:.1%} of portfolio variance"
            )

        if fallback_used:
            logger.warning(
                "%s run_id=%s fallback_reason=%s mode_used=%s",
                RISK_BUDGET_FALLBACK_USED,
                run_id,
                fallback_reason,
                mode_used,
            )

        result = RiskBudgetingRunResult(
            run_id=run_id,
            computed_at=now,
            mode=mode_used,
            strategy_count=len(strategy_ids),
            portfolio_volatility_estimate=port_vol,
            diversification_ratio=diversification_ratio,
            risk_contributions=rc_list,
            recommended_weights=recommended,
            covariance_snapshot_id=cov_snapshot_id,
            covariance_lookback_window=(
                self._config.covariance_lookback_window if cov_snapshot_id else None
            ),
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            iterations_to_convergence=iterations,
            convergence_achieved=converged,
            duration_seconds=round(perf_counter() - wall_start, 4),
            warnings=warnings,
        )

        self._persist(result)
        self._emit_events(result)
        self._record_metrics(result)

        return result

    # ------------------------------------------------------------------
    # Covariance loading
    # ------------------------------------------------------------------

    def _load_covariance(
        self, strategy_ids: list[str]
    ) -> tuple[np.ndarray | None, str | None, FallbackReason | None]:
        """Load the most recent strategy-level covariance snapshot.

        Falls back to symbol-level covariance if no strategy covariance exists.
        Returns (matrix, snapshot_id, fallback_reason).
        """
        snap = self._corr_repo.get_latest_covariance(
            snapshot_type="strategy",
            lookback_window=self._config.covariance_lookback_window,
        )
        if snap is None:
            return None, None, FallbackReason.NO_COVARIANCE_AVAILABLE

        if not snap.is_positive_definite:
            logger.warning(
                "%s snapshot_id=%s condition_number=%s",
                UNSTABLE_COVARIANCE_DETECTED,
                snap.snapshot_id,
                snap.condition_number,
            )
            return None, snap.snapshot_id, FallbackReason.NOT_POSITIVE_DEFINITE

        if snap.condition_number is not None and snap.condition_number > _MAX_CONDITION_NUMBER:
            logger.warning(
                "%s snapshot_id=%s condition_number=%.2e",
                UNSTABLE_COVARIANCE_DETECTED,
                snap.snapshot_id,
                snap.condition_number,
            )
            return None, snap.snapshot_id, FallbackReason.UNSTABLE_COVARIANCE

        cov_dict = snap.covariance_matrix
        universe = snap.asset_universe

        # Check that all requested strategies are in the snapshot
        available = set(universe)
        requested = set(strategy_ids)
        if not requested.issubset(available):
            missing = requested - available
            logger.info(
                "Covariance snapshot %s missing strategies: %s",
                snap.snapshot_id,
                missing,
            )
            return None, snap.snapshot_id, FallbackReason.NO_COVARIANCE_AVAILABLE

        # Extract sub-matrix for the requested strategies in requested order
        try:
            matrix = np.array(
                [[cov_dict[a][b] for b in strategy_ids] for a in strategy_ids],
                dtype=float,
            )
        except (KeyError, TypeError):
            return None, snap.snapshot_id, FallbackReason.SINGULAR_COVARIANCE

        if not np.all(np.isfinite(matrix)):
            return None, snap.snapshot_id, FallbackReason.UNSTABLE_COVARIANCE

        return matrix, snap.snapshot_id, None

    # ------------------------------------------------------------------
    # Volatility loading
    # ------------------------------------------------------------------

    def _load_realized_volatilities(self, strategy_ids: list[str]) -> dict[str, float]:
        snaps = list(
            self._session.scalars(
                select(StrategyLivePerformanceSnapshot)
                .where(StrategyLivePerformanceSnapshot.strategy_id.in_(strategy_ids))
                .where(StrategyLivePerformanceSnapshot.realized_volatility.is_not(None))
                .order_by(
                    StrategyLivePerformanceSnapshot.strategy_id,
                    StrategyLivePerformanceSnapshot.computed_at.desc(),
                )
            ).all()
        )

        # Most recent realized_volatility per strategy
        result: dict[str, float] = {}
        for snap in snaps:
            if snap.strategy_id not in result and snap.realized_volatility is not None:
                result[snap.strategy_id] = float(snap.realized_volatility)
        return result

    # ------------------------------------------------------------------
    # Weight computation
    # ------------------------------------------------------------------

    def _erc_weights(
        self,
        *,
        strategies: list[str],
        cov_matrix: np.ndarray,
        budgets: dict[str, float],
    ) -> _ErcResult:
        """Coordinate Descent ERC solver (Roncalli 2013).

        Each coordinate update exactly minimises the risk parity objective along
        asset i by solving the resulting quadratic in w_i, treating all other
        weights as fixed.  This is monotonically convergent for PD covariance.
        """
        n = len(strategies)
        b = np.array([budgets[s] for s in strategies], dtype=float)
        b = b / b.sum()

        warnings: list[str] = []

        # Start from inverse-vol weights: good initialisation, avoids saddle points
        diag_vols = np.sqrt(np.maximum(np.diag(cov_matrix), 1e-20))
        w = 1.0 / diag_vols
        w = w / w.sum()

        converged = False
        iterations = 0

        for it in range(self._config.erc_max_iterations):
            w_prev = w.copy()

            for i in range(n):
                sigma_w = cov_matrix @ w
                sigma_ii = float(cov_matrix[i, i])
                V = float(w @ sigma_w)

                # Off-diagonal contribution to the marginal risk of asset i
                b_adj = float(sigma_w[i]) - sigma_ii * w[i]

                # Portfolio variance with asset i removed (numerical guard ≥ 0)
                V_minus_i = max(V - 2.0 * b_adj * w[i] - sigma_ii * w[i] ** 2, 0.0)

                # Quadratic: A*w_i^2 + B*w_i + C = 0
                A = sigma_ii * (1.0 - b[i])
                B = b_adj * (1.0 - 2.0 * b[i])
                C = -b[i] * V_minus_i

                if abs(A) < 1e-20:
                    # Linear edge-case (b_i ≈ 1)
                    new_w_i = max(-C / B, _MIN_WEIGHT) if abs(B) > 1e-20 else w[i]
                else:
                    disc = max(B * B - 4.0 * A * C, 0.0)
                    new_w_i = (-B + math.sqrt(disc)) / (2.0 * A)

                w[i] = max(new_w_i, _MIN_WEIGHT)

            # Normalise after each full coordinate sweep
            w = w / w.sum()
            iterations = it + 1

            if float(np.max(np.abs(w - w_prev))) < self._config.erc_convergence_tol:
                converged = True
                break

        return _ErcResult(weights=w, iterations=iterations, converged=converged, warnings=warnings)

    def _inverse_vol_weights(
        self,
        *,
        strategy_ids: list[str],
        vol_by_strategy: dict[str, float],
    ) -> np.ndarray:
        vols = np.array([vol_by_strategy.get(s, 1.0) for s in strategy_ids], dtype=float)
        vols = np.where(vols > 0, vols, 1.0)
        inv_vols = 1.0 / vols
        return inv_vols / inv_vols.sum()

    @staticmethod
    def _equal_weights(n: int) -> np.ndarray:
        return np.ones(n) / n

    def _compute_fallback_weights(
        self,
        *,
        strategy_ids: list[str],
        mode: AllocationMode,
        vol_by_strategy: dict[str, float],
        budgets: dict[str, float],
    ) -> np.ndarray:
        if mode == AllocationMode.INVERSE_VOLATILITY and vol_by_strategy:
            return self._inverse_vol_weights(
                strategy_ids=strategy_ids, vol_by_strategy=vol_by_strategy
            )
        if mode in (AllocationMode.FIXED_RISK_BUDGETS,) and budgets:
            # Use budget proportions as capital proportions (approximation)
            b = np.array([budgets[s] for s in strategy_ids], dtype=float)
            b = b / b.sum()
            return b
        return self._equal_weights(len(strategy_ids))

    def _clamp_and_normalize(self, weights: np.ndarray) -> np.ndarray:
        weights = np.clip(weights, self._config.min_weight, self._config.max_weight)
        total = weights.sum()
        if total <= 0:
            return np.ones(len(weights)) / len(weights)
        return weights / total

    # ------------------------------------------------------------------
    # Risk contribution calculations
    # ------------------------------------------------------------------

    def _compute_risk_contributions(
        self,
        *,
        strategies: list[str],
        weights: np.ndarray,
        cov_matrix: np.ndarray,
        budgets: dict[str, float],
    ) -> tuple[list[StrategyRiskContribution], float | None]:
        """Compute marginal and total risk contributions from covariance matrix."""
        sigma_w = cov_matrix @ weights
        portfolio_variance = float(weights @ sigma_w)
        if portfolio_variance <= 0:
            return [], None

        port_vol = math.sqrt(portfolio_variance * self._config.annualization_factor)

        total_contributions = weights * sigma_w  # element-wise: w_i * (Σw)_i
        pct_contributions = total_contributions / portfolio_variance

        rc_list: list[StrategyRiskContribution] = []
        for i, sid in enumerate(strategies):
            rc_list.append(
                StrategyRiskContribution(
                    strategy_id=sid,
                    capital_weight=round(float(weights[i]), 6),
                    marginal_risk_contribution=round(float(sigma_w[i]), 8),
                    total_risk_contribution=round(float(total_contributions[i]), 8),
                    pct_of_portfolio_variance=round(float(pct_contributions[i]), 6),
                    realized_volatility=None,
                    target_risk_budget=round(budgets[sid], 6),
                    risk_budget_utilization=round(
                        float(pct_contributions[i]) / budgets[sid] if budgets[sid] > 0 else 0.0,
                        4,
                    ),
                )
            )
        return rc_list, port_vol

    def _compute_vol_contributions(
        self,
        *,
        strategies: list[str],
        weights: np.ndarray,
        vol_by_strategy: dict[str, float],
        budgets: dict[str, float],
    ) -> tuple[list[StrategyRiskContribution], float | None]:
        """Diagonal-only risk contributions when no covariance is available."""
        vols = np.array([vol_by_strategy.get(s, 0.0) for s in strategies], dtype=float)
        variances = vols**2
        weighted_var = weights**2 * variances
        total_var = weighted_var.sum()
        if total_var <= 0:
            return [], None

        pct_contributions = weighted_var / total_var
        port_vol = math.sqrt(float(total_var) * self._config.annualization_factor)

        rc_list: list[StrategyRiskContribution] = []
        for i, sid in enumerate(strategies):
            rc_list.append(
                StrategyRiskContribution(
                    strategy_id=sid,
                    capital_weight=round(float(weights[i]), 6),
                    marginal_risk_contribution=round(float(vols[i] ** 2 * weights[i]), 8),
                    total_risk_contribution=round(float(weighted_var[i]), 8),
                    pct_of_portfolio_variance=round(float(pct_contributions[i]), 6),
                    realized_volatility=round(float(vols[i]), 6) if vols[i] > 0 else None,
                    target_risk_budget=round(budgets[sid], 6),
                    risk_budget_utilization=round(
                        float(pct_contributions[i]) / budgets[sid] if budgets[sid] > 0 else 0.0,
                        4,
                    ),
                )
            )
        return rc_list, port_vol

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _diversification_ratio(
        self,
        *,
        strategies: list[str],
        weights: np.ndarray,
        vol_by_strategy: dict[str, float],
        port_vol: float | None,
    ) -> float | None:
        if port_vol is None or port_vol <= 0:
            return None
        vols = np.array([vol_by_strategy.get(s, 0.0) for s in strategies], dtype=float)
        if np.all(vols <= 0):
            return None
        # Weighted average of individual volatilities / portfolio volatility
        weighted_avg_vol = float(weights @ vols) * math.sqrt(self._config.annualization_factor)
        if weighted_avg_vol <= 0:
            return None
        return round(weighted_avg_vol / port_vol, 4)

    @staticmethod
    def _detect_hidden_concentration(rc_list: list[StrategyRiskContribution]) -> bool:
        """Flag when any strategy contributes more than double its equal share of risk.

        With equal capital allocation, a single high-volatility strategy can dominate
        portfolio variance well beyond its proportional share.  The 2× multiplier
        identifies outlier risk contributors while avoiding false positives from minor
        asymmetry in naturally unbalanced portfolios.
        """
        if not rc_list:
            return False
        n = len(rc_list)
        equal_share = 1.0 / n
        return any(rc.pct_of_portfolio_variance > equal_share * 2.0 for rc in rc_list)

    def _resolve_budgets(self, strategy_ids: list[str]) -> dict[str, float]:
        """Return normalized risk budgets for each strategy."""
        configured = self._config.risk_budgets
        if configured:
            # Fill missing with equal share
            total_configured = sum(configured.get(s, 0.0) for s in strategy_ids)
            if total_configured <= 0:
                return {s: 1.0 / len(strategy_ids) for s in strategy_ids}
            raw = {s: configured.get(s, 0.0) for s in strategy_ids}
            total = sum(raw.values())
            return {s: v / total for s, v in raw.items()}
        equal = 1.0 / len(strategy_ids)
        return {s: equal for s in strategy_ids}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, result: RiskBudgetingRunResult) -> None:
        snapshot_id = str(uuid4())
        rc_by_strategy = {
            rc.strategy_id: rc.pct_of_portfolio_variance for rc in result.risk_contributions
        }
        row = RiskBudgetSnapshotRow(
            snapshot_id=snapshot_id,
            run_id=result.run_id,
            computed_at=result.computed_at,
            mode=str(result.mode),
            strategy_universe=list(result.recommended_weights.keys()),
            strategy_count=result.strategy_count,
            target_risk_budgets={
                rc.strategy_id: rc.target_risk_budget for rc in result.risk_contributions
            },
            recommended_weights=result.recommended_weights,
            realized_risk_contributions=rc_by_strategy,
            portfolio_volatility_estimate=result.portfolio_volatility_estimate,
            diversification_ratio=result.diversification_ratio,
            covariance_snapshot_id=result.covariance_snapshot_id,
            covariance_lookback_window=result.covariance_lookback_window,
            fallback_used=result.fallback_used,
            fallback_reason=str(result.fallback_reason) if result.fallback_reason else None,
            convergence_achieved=result.convergence_achieved,
            iterations_to_convergence=result.iterations_to_convergence,
            hidden_concentration_detected=result.hidden_concentration_detected,
            warnings=result.warnings,
            duration_seconds=result.duration_seconds,
            metadata_json={
                "mode": str(result.mode),
                "convergence_achieved": result.convergence_achieved,
            },
        )
        self._repo.insert(row)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _emit_events(self, result: RiskBudgetingRunResult) -> None:
        logger.info(
            "%s run_id=%s mode=%s strategies=%d port_vol=%.4f "
            "fallback=%s converged=%s diversification_ratio=%s "
            "hidden_conc=%s duration_s=%.3f",
            RISK_BUDGET_COMPUTED,
            result.run_id,
            result.mode,
            result.strategy_count,
            result.portfolio_volatility_estimate or 0.0,
            result.fallback_used,
            result.convergence_achieved,
            result.diversification_ratio,
            result.hidden_concentration_detected,
            result.duration_seconds,
        )

        if result.mode in (
            AllocationMode.EQUAL_RISK_CONTRIBUTION,
            AllocationMode.FIXED_RISK_BUDGETS,
        ):
            top_rc = sorted(
                result.risk_contributions,
                key=lambda x: x.pct_of_portfolio_variance,
                reverse=True,
            )[:3]
            top_str = " ".join(
                f"{rc.strategy_id}={rc.pct_of_portfolio_variance:.3f}" for rc in top_rc
            )
            logger.info(
                "%s run_id=%s top_contributors=%s",
                RISK_PARITY_ALLOCATION_COMPUTED,
                result.run_id,
                top_str,
            )

    def _record_metrics(self, result: RiskBudgetingRunResult) -> None:
        if result.portfolio_volatility_estimate is not None:
            ratp_portfolio_volatility_estimate.record(result.portfolio_volatility_estimate)
        if result.diversification_ratio is not None:
            ratp_diversification_ratio.record(result.diversification_ratio)

        ratp_risk_parity_compute_duration_seconds.record(result.duration_seconds)

        for rc in result.risk_contributions:
            ratp_strategy_risk_contribution.record(
                rc.pct_of_portfolio_variance,
                {"strategy_id": rc.strategy_id},
            )
            ratp_risk_budget_utilization.record(
                rc.risk_budget_utilization,
                {"strategy_id": rc.strategy_id},
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_latest_snapshot(
        self, *, mode: AllocationMode | None = None
    ) -> RiskBudgetSnapshotRow | None:
        return self._repo.get_latest(mode=str(mode) if mode is not None else None)

    # ------------------------------------------------------------------
    # Empty result helper
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(
        *,
        run_id: str,
        now: datetime,
        strategy_ids: list[str],
        duration: float,
    ) -> RiskBudgetingRunResult:
        return RiskBudgetingRunResult(
            run_id=run_id,
            computed_at=now,
            mode=AllocationMode.EQUAL_CAPITAL,
            strategy_count=len(strategy_ids),
            portfolio_volatility_estimate=None,
            diversification_ratio=None,
            risk_contributions=[],
            recommended_weights={s: 1.0 / max(len(strategy_ids), 1) for s in strategy_ids},
            covariance_snapshot_id=None,
            covariance_lookback_window=None,
            fallback_used=True,
            fallback_reason=FallbackReason.INSUFFICIENT_HISTORY,
            iterations_to_convergence=None,
            convergence_achieved=False,
            duration_seconds=round(duration, 4),
            warnings=["Fewer than 2 strategies provided; returning equal capital weights"],
        )

    # ------------------------------------------------------------------
    # Static utilities
    # ------------------------------------------------------------------

    @staticmethod
    def result_to_jsonable(result: RiskBudgetingRunResult) -> dict[str, Any]:
        return dict(result.model_dump(mode="json"))
