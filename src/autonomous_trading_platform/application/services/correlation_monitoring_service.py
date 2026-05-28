from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.correlation_snapshot import (
    ClusterInfo,
    CorrelatedPair,
    CorrelationMonitoringRunResult,
    CorrelationSnapshotRecord,
    CorrelationSnapshotType,
    CovarianceSnapshotRecord,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_average_portfolio_correlation,
    ratp_correlation_cluster_count,
    ratp_covariance_snapshot_duration_seconds,
    ratp_max_strategy_correlation,
    ratp_max_symbol_correlation,
)
from autonomous_trading_platform.storage.sor.models.correlation_snapshots import (
    CorrelationSnapshotRow,
    CovarianceSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.correlation_snapshot_repository import (
    CorrelationSnapshotRepository,
)

logger = get_logger(__name__)

# Structured audit event names
CORRELATION_SNAPSHOT_COMPUTED = "CORRELATION_SNAPSHOT_COMPUTED"
COVARIANCE_SNAPSHOT_COMPUTED = "COVARIANCE_SNAPSHOT_COMPUTED"
CORRELATION_REGIME_SHIFT_DETECTED = "CORRELATION_REGIME_SHIFT_DETECTED"
HIGH_CORRELATION_CLUSTER_DETECTED = "HIGH_CORRELATION_CLUSTER_DETECTED"

_DEFAULT_WINDOWS = [20, 60, 252]
_TOP_PAIRS_LIMIT = 10
_CLUSTER_CORRELATION_THRESHOLD = 0.80
_HIGH_CORR_ALERT_THRESHOLD = 0.90
_MAX_CONDITION_NUMBER = 1e12


@dataclass(frozen=True)
class CorrelationMonitoringConfig:
    """Configures rolling windows, minimum observations, and clustering thresholds."""

    symbol_windows: list[int] = field(default_factory=lambda: list(_DEFAULT_WINDOWS))
    strategy_windows: list[int] = field(default_factory=lambda: list(_DEFAULT_WINDOWS))
    sector_windows: list[int] = field(default_factory=lambda: list(_DEFAULT_WINDOWS))
    min_observations: int = 20
    annualization_factor: int = 252
    cluster_correlation_threshold: float = _CLUSTER_CORRELATION_THRESHOLD
    top_pairs_limit: int = _TOP_PAIRS_LIMIT
    bar_interval: str = "1d"
    price_basis: str = "adjusted"
    regime_label: str | None = None


@dataclass(frozen=True)
class _MatrixResult:
    assets: list[str]
    matrix: np.ndarray
    observations: int
    is_stable: bool
    condition_number: float | None
    warnings: list[str]


class CorrelationMonitoringService:
    """Computes and persists rolling correlation and covariance snapshots.

    Supports symbol-level, strategy-level, and sector-level correlations.
    Designed for scheduled runtime computation and historical replay.
    Does NOT alter allocation or trading behavior — observability only.
    """

    def __init__(
        self,
        *,
        session: Session,
        config: CorrelationMonitoringConfig | None = None,
        correlation_repo: CorrelationSnapshotRepository | None = None,
    ) -> None:
        self._session = session
        self._config = config or CorrelationMonitoringConfig()
        self._repo = correlation_repo or CorrelationSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        as_of: datetime,
        portfolio_symbols: list[str],
        strategy_ids: list[str] | None = None,
        sector_map: dict[str, str] | None = None,
        run_id: str | None = None,
    ) -> CorrelationMonitoringRunResult:
        """Compute all configured correlation/covariance snapshots and persist them.

        Args:
            as_of: Reference timestamp for the lookback calculation.
            portfolio_symbols: Symbols to include in symbol-level matrices.
            strategy_ids: Optional strategy IDs for strategy-level correlations.
            sector_map: Optional {symbol: sector} mapping for sector aggregation.
            run_id: Caller-supplied run ID for lineage tracing.
        """
        if run_id is None:
            run_id = str(uuid4())

        wall_start = perf_counter()
        now = datetime.now(UTC)

        strategy_ids = strategy_ids or []
        sector_map = sector_map or {}

        symbol_snapshots: list[CorrelationSnapshotRecord] = []
        strategy_snapshots: list[CorrelationSnapshotRecord] = []
        sector_snapshots: list[CorrelationSnapshotRecord] = []
        cov_snapshots: list[CovarianceSnapshotRecord] = []
        all_warnings: list[str] = []

        # --- Symbol correlations ---
        if portfolio_symbols:
            for window in self._config.symbol_windows:
                corr_rec, cov_rec = self._compute_symbol_snapshots(
                    symbols=portfolio_symbols,
                    as_of=as_of,
                    window=window,
                    run_id=run_id,
                )
                if corr_rec is not None:
                    symbol_snapshots.append(corr_rec)
                    all_warnings.extend(corr_rec.warnings)
                    self._persist_correlation(corr_rec)
                if cov_rec is not None:
                    cov_snapshots.append(cov_rec)
                    all_warnings.extend(cov_rec.warnings)
                    self._persist_covariance(cov_rec)

        # --- Strategy correlations ---
        if strategy_ids:
            for window in self._config.strategy_windows:
                corr_rec, cov_rec = self._compute_strategy_snapshots(
                    strategy_ids=strategy_ids,
                    as_of=as_of,
                    window=window,
                    run_id=run_id,
                )
                if corr_rec is not None:
                    strategy_snapshots.append(corr_rec)
                    all_warnings.extend(corr_rec.warnings)
                    self._persist_correlation(corr_rec)
                if cov_rec is not None:
                    cov_snapshots.append(cov_rec)
                    self._persist_covariance(cov_rec)

        # --- Sector correlations ---
        if sector_map:
            for window in self._config.sector_windows:
                corr_rec, cov_rec = self._compute_sector_snapshots(
                    symbol_returns_cache=self._load_symbol_returns(
                        symbols=list(sector_map.keys()), as_of=as_of, window=window
                    ),
                    sector_map=sector_map,
                    as_of=as_of,
                    window=window,
                    run_id=run_id,
                )
                if corr_rec is not None:
                    sector_snapshots.append(corr_rec)
                    all_warnings.extend(corr_rec.warnings)
                    self._persist_correlation(corr_rec)
                if cov_rec is not None:
                    cov_snapshots.append(cov_rec)
                    self._persist_covariance(cov_rec)

        # --- Aggregate diagnostics ---
        top_symbol_pairs = self._get_top_pairs_from_snapshots(
            symbol_snapshots, self._config.top_pairs_limit
        )
        top_strategy_pairs = self._get_top_pairs_from_snapshots(
            strategy_snapshots, self._config.top_pairs_limit
        )

        avg_portfolio_corr = self._agg_average_correlation(symbol_snapshots)
        max_strategy_corr = self._agg_max_correlation(strategy_snapshots)
        max_symbol_corr = self._agg_max_correlation(symbol_snapshots)

        total_clusters = sum(s.cluster_count for s in symbol_snapshots + strategy_snapshots)
        hidden_cluster = any(s.cluster_count >= 1 for s in symbol_snapshots + strategy_snapshots)

        # --- Structured log events ---
        self._emit_run_events(
            run_id=run_id,
            symbol_snapshots=symbol_snapshots,
            strategy_snapshots=strategy_snapshots,
            top_symbol_pairs=top_symbol_pairs,
            hidden_cluster=hidden_cluster,
        )

        # --- OTel metrics ---
        self._record_metrics(
            avg_portfolio_corr=avg_portfolio_corr,
            max_strategy_corr=max_strategy_corr,
            max_symbol_corr=max_symbol_corr,
            cluster_count=total_clusters,
            cov_duration=perf_counter() - wall_start,
        )

        total_persisted = (
            len(symbol_snapshots)
            + len(strategy_snapshots)
            + len(sector_snapshots)
            + len(cov_snapshots)
        )

        return CorrelationMonitoringRunResult(
            run_id=run_id,
            computed_at=now,
            snapshots_persisted=total_persisted,
            symbol_snapshot_count=len(symbol_snapshots),
            strategy_snapshot_count=len(strategy_snapshots),
            sector_snapshot_count=len(sector_snapshots),
            covariance_snapshot_count=len(cov_snapshots),
            duration_seconds=perf_counter() - wall_start,
            top_correlated_symbol_pairs=top_symbol_pairs,
            top_correlated_strategy_pairs=top_strategy_pairs,
            hidden_cluster_detected=hidden_cluster,
            cluster_count=total_clusters,
            average_portfolio_correlation=avg_portfolio_corr,
            max_strategy_correlation=max_strategy_corr,
            max_symbol_correlation=max_symbol_corr,
            warnings=all_warnings,
        )

    # ------------------------------------------------------------------
    # Symbol-level snapshots
    # ------------------------------------------------------------------

    def _compute_symbol_snapshots(
        self,
        *,
        symbols: list[str],
        as_of: datetime,
        window: int,
        run_id: str,
    ) -> tuple[CorrelationSnapshotRecord | None, CovarianceSnapshotRecord | None]:
        returns_by_symbol = self._load_symbol_returns(symbols=symbols, as_of=as_of, window=window)
        if not returns_by_symbol:
            return None, None

        matrix_result = self._build_return_matrix(returns_by_symbol)
        if matrix_result is None:
            return None, None

        corr_rec = self._build_correlation_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.SYMBOL,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        cov_rec = self._build_covariance_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.SYMBOL,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        return corr_rec, cov_rec

    # ------------------------------------------------------------------
    # Strategy-level snapshots
    # ------------------------------------------------------------------

    def _compute_strategy_snapshots(
        self,
        *,
        strategy_ids: list[str],
        as_of: datetime,
        window: int,
        run_id: str,
    ) -> tuple[CorrelationSnapshotRecord | None, CovarianceSnapshotRecord | None]:
        returns_by_strategy = self._load_strategy_returns(
            strategy_ids=strategy_ids, as_of=as_of, window=window
        )
        if not returns_by_strategy:
            return None, None

        matrix_result = self._build_return_matrix(returns_by_strategy)
        if matrix_result is None:
            return None, None

        corr_rec = self._build_correlation_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.STRATEGY,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        cov_rec = self._build_covariance_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.STRATEGY,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        return corr_rec, cov_rec

    # ------------------------------------------------------------------
    # Sector-level snapshots
    # ------------------------------------------------------------------

    def _compute_sector_snapshots(
        self,
        *,
        symbol_returns_cache: dict[str, list[float]],
        sector_map: dict[str, str],
        as_of: datetime,
        window: int,
        run_id: str,
    ) -> tuple[CorrelationSnapshotRecord | None, CovarianceSnapshotRecord | None]:
        sector_returns = self._aggregate_sector_returns(
            symbol_returns_cache=symbol_returns_cache,
            sector_map=sector_map,
        )
        if not sector_returns:
            return None, None

        matrix_result = self._build_return_matrix(sector_returns)
        if matrix_result is None:
            return None, None

        corr_rec = self._build_correlation_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.SECTOR,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        cov_rec = self._build_covariance_record(
            matrix_result=matrix_result,
            snapshot_type=CorrelationSnapshotType.SECTOR,
            lookback_window=window,
            as_of=as_of,
            run_id=run_id,
        )
        return corr_rec, cov_rec

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_symbol_returns(
        self,
        *,
        symbols: list[str],
        as_of: datetime,
        window: int,
    ) -> dict[str, list[float]]:
        """Fetch close prices from market_bars and compute log returns."""
        if not symbols:
            return {}

        bars = list(
            self._session.scalars(
                select(MarketBar)
                .where(MarketBar.symbol.in_(symbols))
                .where(MarketBar.interval == self._config.bar_interval)
                .where(MarketBar.price_basis == self._config.price_basis)
                .where(MarketBar.timestamp <= as_of)
                .order_by(MarketBar.symbol, MarketBar.timestamp.asc())
            ).all()
        )

        prices_by_symbol: dict[str, list[float]] = {}
        for bar in bars:
            sym = bar.symbol
            if sym not in prices_by_symbol:
                prices_by_symbol[sym] = []
            prices_by_symbol[sym].append(float(bar.close))

        # Trim to most recent window+1 prices (need N+1 prices for N returns)
        returns_by_symbol: dict[str, list[float]] = {}
        for sym, prices in prices_by_symbol.items():
            tail = prices[-(window + 1) :]
            if len(tail) >= 2:
                returns_by_symbol[sym] = _log_returns(tail)

        return returns_by_symbol

    def _load_strategy_returns(
        self,
        *,
        strategy_ids: list[str],
        as_of: datetime,
        window: int,
    ) -> dict[str, list[float]]:
        """Fetch per-strategy realized_return from live performance snapshots."""
        if not strategy_ids:
            return {}

        snapshots = list(
            self._session.scalars(
                select(StrategyLivePerformanceSnapshot)
                .where(StrategyLivePerformanceSnapshot.strategy_id.in_(strategy_ids))
                .where(StrategyLivePerformanceSnapshot.computed_at <= as_of)
                .where(StrategyLivePerformanceSnapshot.realized_return.is_not(None))
                .order_by(
                    StrategyLivePerformanceSnapshot.strategy_id,
                    StrategyLivePerformanceSnapshot.computed_at.asc(),
                )
            ).all()
        )

        returns_by_strategy: dict[str, list[float]] = {}
        for snap in snapshots:
            sid = snap.strategy_id
            if sid not in returns_by_strategy:
                returns_by_strategy[sid] = []
            if snap.realized_return is not None:
                returns_by_strategy[sid].append(float(snap.realized_return))

        # Trim to window
        return {
            sid: rets[-window:]
            for sid, rets in returns_by_strategy.items()
            if len(rets) >= self._config.min_observations
        }

    def _aggregate_sector_returns(
        self,
        *,
        symbol_returns_cache: dict[str, list[float]],
        sector_map: dict[str, str],
    ) -> dict[str, list[float]]:
        """Average symbol returns within each sector to produce sector return series."""
        sector_symbol_returns: dict[str, list[list[float]]] = {}
        for sym, sector in sector_map.items():
            if sym in symbol_returns_cache:
                if sector not in sector_symbol_returns:
                    sector_symbol_returns[sector] = []
                sector_symbol_returns[sector].append(symbol_returns_cache[sym])

        sector_returns: dict[str, list[float]] = {}
        for sector, series_list in sector_symbol_returns.items():
            if not series_list:
                continue
            min_len = min(len(s) for s in series_list)
            if min_len < self._config.min_observations:
                continue
            trimmed = [s[-min_len:] for s in series_list]
            avg = [sum(vals) / len(vals) for vals in zip(*trimmed, strict=False)]
            sector_returns[sector] = avg

        return sector_returns

    # ------------------------------------------------------------------
    # Matrix computation
    # ------------------------------------------------------------------

    def _build_return_matrix(
        self, returns_by_asset: dict[str, list[float]]
    ) -> _MatrixResult | None:
        """Align return series and build the N×T return matrix for np.corrcoef."""
        if len(returns_by_asset) < 2:
            return None

        # Align all series to the minimum common length
        min_len = min(len(r) for r in returns_by_asset.values())
        if min_len < self._config.min_observations:
            return None

        assets = sorted(returns_by_asset.keys())
        aligned = np.array(
            [returns_by_asset[a][-min_len:] for a in assets], dtype=float
        )  # shape: (n_assets, n_obs)

        warnings: list[str] = []

        # Guard: NaN/Inf
        if not np.all(np.isfinite(aligned)):
            nan_assets = [
                assets[i] for i in range(len(assets)) if not np.all(np.isfinite(aligned[i]))
            ]
            for a in nan_assets:
                warnings.append(f"Non-finite returns for {a}; asset excluded from matrix")
            mask = [np.all(np.isfinite(aligned[i])) for i in range(len(assets))]
            assets = [a for a, m in zip(assets, mask, strict=True) if m]
            aligned = aligned[[m for m in mask]]
            if len(assets) < 2:
                return None

        # Guard: zero-variance series
        variances = np.var(aligned, axis=1)
        zero_var_mask = variances < 1e-12
        if np.any(zero_var_mask):
            zero_assets = [assets[i] for i in range(len(assets)) if zero_var_mask[i]]
            for a in zero_assets:
                warnings.append(f"Zero-variance return series for {a}; excluded from matrix")
            keep = ~zero_var_mask
            assets = [a for a, k in zip(assets, keep, strict=True) if k]
            aligned = aligned[keep]
            if len(assets) < 2:
                return None

        # Condition number on covariance
        cov = np.cov(aligned)
        is_stable = True
        condition_number: float | None = None
        try:
            condition_number = float(np.linalg.cond(cov))
            if condition_number > _MAX_CONDITION_NUMBER:
                is_stable = False
                warnings.append(
                    f"Covariance matrix condition number {condition_number:.2e} exceeds threshold"
                )
        except np.linalg.LinAlgError:
            is_stable = False
            warnings.append("Could not compute condition number for covariance matrix")

        return _MatrixResult(
            assets=assets,
            matrix=aligned,
            observations=min_len,
            is_stable=is_stable,
            condition_number=condition_number,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Record builders
    # ------------------------------------------------------------------

    def _build_correlation_record(
        self,
        *,
        matrix_result: _MatrixResult,
        snapshot_type: CorrelationSnapshotType,
        lookback_window: int,
        as_of: datetime,
        run_id: str,
    ) -> CorrelationSnapshotRecord:
        start = perf_counter()

        corr_matrix = np.corrcoef(matrix_result.matrix)  # shape: (n, n)
        n = len(matrix_result.assets)

        # Extract off-diagonal elements
        off_diag = [
            corr_matrix[i, j]
            for i in range(n)
            for j in range(i + 1, n)
            if math.isfinite(corr_matrix[i, j])
        ]

        avg_corr = float(np.mean(off_diag)) if off_diag else 0.0
        max_corr = float(max(off_diag)) if off_diag else 0.0
        min_corr = float(min(off_diag)) if off_diag else 0.0

        # Top correlated pairs
        pairs: list[CorrelatedPair] = []
        for i in range(n):
            for j in range(i + 1, n):
                c = float(corr_matrix[i, j])
                if math.isfinite(c):
                    pairs.append(
                        CorrelatedPair(
                            asset_a=matrix_result.assets[i],
                            asset_b=matrix_result.assets[j],
                            correlation=c,
                            abs_correlation=abs(c),
                        )
                    )
        pairs.sort(key=lambda p: p.abs_correlation, reverse=True)
        top_pairs = pairs[: self._config.top_pairs_limit]

        # Cluster detection (greedy union-find by threshold)
        clusters = _detect_clusters(
            assets=matrix_result.assets,
            corr_matrix=corr_matrix,
            threshold=self._config.cluster_correlation_threshold,
        )

        # Serialize matrix as {asset: {asset: value}}
        matrix_dict: dict[str, dict[str, float]] = {}
        for i, a in enumerate(matrix_result.assets):
            matrix_dict[a] = {
                matrix_result.assets[j]: round(float(corr_matrix[i, j]), 6) for j in range(n)
            }

        duration = perf_counter() - start

        return CorrelationSnapshotRecord(
            snapshot_id=str(uuid4()),
            snapshot_type=snapshot_type,
            lookback_window=lookback_window,
            computed_at=datetime.now(UTC),
            as_of_date=as_of,
            asset_universe=matrix_result.assets,
            regime_label=self._config.regime_label,
            asset_count=n,
            observations_used=matrix_result.observations,
            average_pairwise_correlation=round(avg_corr, 6),
            max_pairwise_correlation=round(max_corr, 6),
            min_pairwise_correlation=round(min_corr, 6),
            correlation_matrix=matrix_dict,
            top_correlated_pairs=top_pairs,
            clusters=clusters,
            cluster_count=len(clusters),
            is_numerically_stable=matrix_result.is_stable,
            condition_number=matrix_result.condition_number,
            warnings=list(matrix_result.warnings),
            computation_duration_seconds=round(duration, 4),
            run_id=run_id,
        )

    def _build_covariance_record(
        self,
        *,
        matrix_result: _MatrixResult,
        snapshot_type: CorrelationSnapshotType,
        lookback_window: int,
        as_of: datetime,
        run_id: str,
    ) -> CovarianceSnapshotRecord:
        start = perf_counter()
        af = self._config.annualization_factor
        n = len(matrix_result.assets)

        cov_matrix = np.cov(matrix_result.matrix) * af

        # Positive-definite check via eigenvalue sign
        try:
            eigenvalues = np.linalg.eigvalsh(cov_matrix)
            is_positive_definite = bool(np.all(eigenvalues > 0))
        except np.linalg.LinAlgError:
            is_positive_definite = False

        warnings = list(matrix_result.warnings)
        if not is_positive_definite:
            warnings.append("Annualised covariance matrix is not positive-definite")

        # Serialize and hash
        matrix_dict: dict[str, dict[str, float]] = {}
        for i, a in enumerate(matrix_result.assets):
            matrix_dict[a] = {
                matrix_result.assets[j]: round(float(cov_matrix[i, j]), 8) for j in range(n)
            }

        matrix_json_str = json.dumps(matrix_dict, sort_keys=True)
        matrix_hash = hashlib.sha256(matrix_json_str.encode()).hexdigest()[:16]

        duration = perf_counter() - start

        return CovarianceSnapshotRecord(
            snapshot_id=str(uuid4()),
            snapshot_type=snapshot_type,
            lookback_window=lookback_window,
            computed_at=datetime.now(UTC),
            as_of_date=as_of,
            asset_universe=matrix_result.assets,
            asset_count=n,
            observations_used=matrix_result.observations,
            annualization_factor=af,
            covariance_matrix=matrix_dict,
            matrix_hash=matrix_hash,
            is_positive_definite=is_positive_definite,
            condition_number=matrix_result.condition_number,
            warnings=warnings,
            computation_duration_seconds=round(duration, 4),
            run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_correlation(self, record: CorrelationSnapshotRecord) -> None:
        row = CorrelationSnapshotRow(
            snapshot_id=record.snapshot_id,
            snapshot_type=str(record.snapshot_type),
            lookback_window=record.lookback_window,
            computed_at=record.computed_at,
            as_of_date=record.as_of_date,
            asset_universe=record.asset_universe,
            asset_count=record.asset_count,
            observations_used=record.observations_used,
            average_pairwise_correlation=record.average_pairwise_correlation,
            max_pairwise_correlation=record.max_pairwise_correlation,
            min_pairwise_correlation=record.min_pairwise_correlation,
            correlation_matrix=record.correlation_matrix,
            top_correlated_pairs=[p.model_dump() for p in record.top_correlated_pairs],
            clusters=[c.model_dump() for c in record.clusters],
            cluster_count=record.cluster_count,
            is_numerically_stable=record.is_numerically_stable,
            condition_number=record.condition_number,
            regime_label=record.regime_label,
            warnings=record.warnings,
            computation_duration_seconds=record.computation_duration_seconds,
            run_id=record.run_id,
        )
        self._repo.insert_correlation(row)

    def _persist_covariance(self, record: CovarianceSnapshotRecord) -> None:
        row = CovarianceSnapshotRow(
            snapshot_id=record.snapshot_id,
            snapshot_type=str(record.snapshot_type),
            lookback_window=record.lookback_window,
            computed_at=record.computed_at,
            as_of_date=record.as_of_date,
            asset_universe=record.asset_universe,
            asset_count=record.asset_count,
            observations_used=record.observations_used,
            annualization_factor=record.annualization_factor,
            covariance_matrix=record.covariance_matrix,
            matrix_hash=record.matrix_hash,
            is_positive_definite=record.is_positive_definite,
            condition_number=record.condition_number,
            warnings=record.warnings,
            computation_duration_seconds=record.computation_duration_seconds,
            run_id=record.run_id,
        )
        self._repo.insert_covariance(row)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _emit_run_events(
        self,
        *,
        run_id: str,
        symbol_snapshots: list[CorrelationSnapshotRecord],
        strategy_snapshots: list[CorrelationSnapshotRecord],
        top_symbol_pairs: list[CorrelatedPair],
        hidden_cluster: bool,
    ) -> None:
        for snap in symbol_snapshots:
            logger.info(
                "%s run_id=%s type=%s window=%d assets=%d obs=%d avg_corr=%.4f max_corr=%.4f "
                "clusters=%d stable=%s duration_s=%.3f",
                CORRELATION_SNAPSHOT_COMPUTED,
                run_id,
                snap.snapshot_type,
                snap.lookback_window,
                snap.asset_count,
                snap.observations_used,
                snap.average_pairwise_correlation,
                snap.max_pairwise_correlation,
                snap.cluster_count,
                snap.is_numerically_stable,
                snap.computation_duration_seconds,
            )

        for snap in strategy_snapshots:
            logger.info(
                "%s run_id=%s type=%s window=%d strategies=%d avg_corr=%.4f max_corr=%.4f",
                CORRELATION_SNAPSHOT_COMPUTED,
                run_id,
                snap.snapshot_type,
                snap.lookback_window,
                snap.asset_count,
                snap.average_pairwise_correlation,
                snap.max_pairwise_correlation,
            )

        if hidden_cluster:
            top_pair_strs = [
                f"{p.asset_a}/{p.asset_b}={p.correlation:.3f}" for p in top_symbol_pairs[:3]
            ]
            logger.warning(
                "%s run_id=%s top_pairs=%s",
                HIGH_CORRELATION_CLUSTER_DETECTED,
                run_id,
                ",".join(top_pair_strs),
            )

    def _record_metrics(
        self,
        *,
        avg_portfolio_corr: float | None,
        max_strategy_corr: float | None,
        max_symbol_corr: float | None,
        cluster_count: int,
        cov_duration: float,
    ) -> None:
        if avg_portfolio_corr is not None:
            ratp_average_portfolio_correlation.record(avg_portfolio_corr)
        if max_strategy_corr is not None:
            ratp_max_strategy_correlation.record(max_strategy_corr)
        if max_symbol_corr is not None:
            ratp_max_symbol_correlation.record(max_symbol_corr)
        ratp_correlation_cluster_count.record(float(cluster_count))
        ratp_covariance_snapshot_duration_seconds.record(max(0.0, cov_duration))

    # ------------------------------------------------------------------
    # Query helpers (for API / diagnostics)
    # ------------------------------------------------------------------

    def get_latest_symbol_correlation(
        self, *, lookback_window: int = 60
    ) -> CorrelationSnapshotRow | None:
        return self._repo.get_latest_correlation(
            snapshot_type=str(CorrelationSnapshotType.SYMBOL),
            lookback_window=lookback_window,
        )

    def get_latest_strategy_correlation(
        self, *, lookback_window: int = 60
    ) -> CorrelationSnapshotRow | None:
        return self._repo.get_latest_correlation(
            snapshot_type=str(CorrelationSnapshotType.STRATEGY),
            lookback_window=lookback_window,
        )

    def get_latest_covariance(
        self, *, snapshot_type: str = "symbol", lookback_window: int = 60
    ) -> CovarianceSnapshotRow | None:
        return self._repo.get_latest_covariance(
            snapshot_type=snapshot_type,
            lookback_window=lookback_window,
        )

    # ------------------------------------------------------------------
    # Utility / static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def result_to_jsonable(result: CorrelationMonitoringRunResult) -> dict[str, Any]:
        return dict(result.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Internal aggregation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_top_pairs_from_snapshots(
        snapshots: list[CorrelationSnapshotRecord], limit: int
    ) -> list[CorrelatedPair]:
        if not snapshots:
            return []
        all_pairs: list[CorrelatedPair] = []
        for snap in snapshots:
            all_pairs.extend(snap.top_correlated_pairs)
        all_pairs.sort(key=lambda p: p.abs_correlation, reverse=True)
        seen: set[frozenset[str]] = set()
        deduplicated: list[CorrelatedPair] = []
        for pair in all_pairs:
            key = frozenset([pair.asset_a, pair.asset_b])
            if key not in seen:
                seen.add(key)
                deduplicated.append(pair)
            if len(deduplicated) >= limit:
                break
        return deduplicated

    @staticmethod
    def _agg_average_correlation(
        snapshots: list[CorrelationSnapshotRecord],
    ) -> float | None:
        if not snapshots:
            return None
        values = [s.average_pairwise_correlation for s in snapshots]
        return float(np.mean(values))

    @staticmethod
    def _agg_max_correlation(
        snapshots: list[CorrelationSnapshotRecord],
    ) -> float | None:
        if not snapshots:
            return None
        return float(max(s.max_pairwise_correlation for s in snapshots))


# ------------------------------------------------------------------
# Module-level pure functions
# ------------------------------------------------------------------


def _log_returns(prices: list[float]) -> list[float]:
    """Compute log returns from a price series. Returns length = len(prices) - 1."""
    out = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 > 0 and p1 > 0:
            out.append(math.log(p1 / p0))
        else:
            out.append(0.0)
    return out


def _detect_clusters(
    *,
    assets: list[str],
    corr_matrix: np.ndarray,
    threshold: float,
) -> list[ClusterInfo]:
    """Greedy single-linkage clustering: two assets join a cluster when their
    pairwise |correlation| exceeds `threshold`."""
    n = len(assets)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr_matrix[i, j]) >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        root = find(idx)
        groups.setdefault(root, []).append(idx)

    clusters: list[ClusterInfo] = []
    cluster_id = 0
    for group_indices in groups.values():
        if len(group_indices) < 2:
            continue
        members = [assets[i] for i in group_indices]
        pairs_corr = [
            abs(corr_matrix[group_indices[a], group_indices[b]])
            for a in range(len(group_indices))
            for b in range(a + 1, len(group_indices))
        ]
        avg_intra = float(np.mean(pairs_corr)) if pairs_corr else threshold
        clusters.append(
            ClusterInfo(
                cluster_id=cluster_id,
                members=members,
                avg_intra_cluster_correlation=round(avg_intra, 4),
            )
        )
        cluster_id += 1

    return clusters
