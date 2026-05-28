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

from autonomous_trading_platform.contracts.runtime.factor_exposure import (
    FactorExposureInputPosition,
    FactorExposureRunResult,
    FactorExposureSnapshotRecord,
    FactorExposureValue,
    FactorName,
    PortfolioFactorExposure,
    StrategyFactorExposure,
    SymbolFactorExposure,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_factor_concentration_alerts_total,
    ratp_factor_exposure_compute_duration_seconds,
    ratp_portfolio_beta_exposure,
    ratp_portfolio_momentum_exposure,
    ratp_portfolio_volatility_exposure,
)
from autonomous_trading_platform.storage.sor.models.factor_exposure_snapshots import (
    FactorExposureSnapshotRow,
    PortfolioFactorExposureRow,
    StrategyFactorExposureRow,
)
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.repositories.core.factor_exposure_snapshot_repository import (
    FactorExposureSnapshotRepository,
)

logger = get_logger(__name__)

FACTOR_EXPOSURE_COMPUTED = "FACTOR_EXPOSURE_COMPUTED"
FACTOR_CONCENTRATION_DETECTED = "FACTOR_CONCENTRATION_DETECTED"
FACTOR_DRIFT_DETECTED = "FACTOR_DRIFT_DETECTED"
BETA_EXPOSURE_THRESHOLD_EXCEEDED = "BETA_EXPOSURE_THRESHOLD_EXCEEDED"

FACTOR_COMPUTATION_VERSION = "factor_exposure_v1"

_DEFAULT_WINDOWS = [20, 60, 252]
_MAX_ABS_BETA = 10.0
_MIN_BENCHMARK_VARIANCE = 1e-12


@dataclass(frozen=True)
class FactorExposureThresholds:
    max_abs_beta_exposure: float | None = 1.25
    max_abs_momentum_exposure: float | None = 0.20
    max_volatility_tilt: float | None = 0.60
    max_sector_concentration: float | None = 0.50
    drift_threshold: float | None = 0.25


@dataclass(frozen=True)
class FactorExposureMonitoringConfig:
    """Configures explicit rolling factor exposure methodology.

    Methodology:
    - market_beta: ordinary least squares beta against the configured benchmark
      using aligned rolling log returns.
    - momentum: trailing cumulative return over the lookback window.
    - volatility: annualized realized volatility of rolling log returns.
    - sector: absolute portfolio weight by sector.
    - size: log(market_cap), weighted across symbols when metadata exists.
    - quality/value: optional metadata passthrough for later institutional models.
    """

    factor_exposure_windows: list[int] = field(default_factory=lambda: list(_DEFAULT_WINDOWS))
    beta_lookback_window: int | None = None
    minimum_observations_required: int = 20
    benchmark_symbol: str = "SPY"
    benchmark_source: str = "market_bars"
    bar_interval: str = "1d"
    price_basis: str = "adjusted"
    annualization_factor: int = 252
    thresholds: FactorExposureThresholds = field(default_factory=FactorExposureThresholds)


@dataclass(frozen=True)
class _SymbolSeries:
    returns: list[float]
    prices: list[float]


class FactorExposureMonitoringService:
    """Computes and persists rolling factor exposure snapshots.

    This is observability-only infrastructure. It does not block orders, mutate
    allocations, or introduce factor-neutral optimization constraints.
    """

    def __init__(
        self,
        *,
        session: Session,
        config: FactorExposureMonitoringConfig | None = None,
        repo: FactorExposureSnapshotRepository | None = None,
    ) -> None:
        self._session = session
        self._config = config or FactorExposureMonitoringConfig()
        self._repo = repo or FactorExposureSnapshotRepository(session)

    def run(
        self,
        *,
        as_of: datetime,
        positions: list[FactorExposureInputPosition],
        portfolio_id: str | None = None,
        run_id: str | None = None,
    ) -> FactorExposureRunResult:
        if run_id is None:
            run_id = str(uuid4())

        wall_start = perf_counter()
        now = datetime.now(UTC)
        normalized_positions = self._normalize_positions(positions)
        all_warnings: list[str] = []
        concentration_alerts: list[dict[str, Any]] = []
        drift_alerts: list[dict[str, Any]] = []
        portfolio_exposures: dict[int, dict[str, float | None]] = {}
        snapshots_persisted = 0

        if not normalized_positions:
            return FactorExposureRunResult(
                run_id=run_id,
                computed_at=now,
                snapshots_persisted=0,
                portfolio_id=portfolio_id,
                benchmark_symbol=self._config.benchmark_symbol,
                lookback_windows=list(self._config.factor_exposure_windows),
                portfolio_exposures={},
                concentration_alerts=[],
                drift_alerts=[],
                warnings=["No portfolio positions supplied for factor exposure monitoring"],
                duration_seconds=round(perf_counter() - wall_start, 4),
            )

        max_window = max(self._config.factor_exposure_windows + [self._beta_window()])
        symbols = sorted({p.symbol for p in normalized_positions} | {self._config.benchmark_symbol})
        series_by_symbol = self._load_series(symbols=symbols, as_of=as_of, window=max_window)

        for window in self._config.factor_exposure_windows:
            record = self._compute_snapshot(
                as_of=as_of,
                computed_at=now,
                portfolio_id=portfolio_id,
                run_id=run_id,
                positions=normalized_positions,
                series_by_symbol=series_by_symbol,
                lookback_window=window,
            )
            all_warnings.extend(record.warnings)
            concentration_alerts.extend(record.portfolio_exposure.concentration_diagnostics)
            drift_alerts.extend(
                self._detect_drift(
                    portfolio_id=portfolio_id,
                    lookback_window=window,
                    current_exposures=record.portfolio_exposure.exposures,
                )
            )
            portfolio_exposures[window] = record.portfolio_exposure.exposures
            self._persist(record)
            self._emit_snapshot_events(record)
            self._record_snapshot_metrics(record)
            snapshots_persisted += 1

        for alert in drift_alerts:
            logger.warning(
                "%s run_id=%s factor=%s drift=%.4f threshold=%.4f lookback_window=%s",
                FACTOR_DRIFT_DETECTED,
                run_id,
                alert.get("factor_name"),
                alert.get("drift", 0.0),
                alert.get("threshold", 0.0),
                alert.get("lookback_window"),
            )

        return FactorExposureRunResult(
            run_id=run_id,
            computed_at=now,
            snapshots_persisted=snapshots_persisted,
            portfolio_id=portfolio_id,
            benchmark_symbol=self._config.benchmark_symbol,
            lookback_windows=list(self._config.factor_exposure_windows),
            portfolio_exposures=portfolio_exposures,
            concentration_alerts=concentration_alerts,
            drift_alerts=drift_alerts,
            warnings=all_warnings,
            duration_seconds=round(perf_counter() - wall_start, 4),
        )

    def _compute_snapshot(
        self,
        *,
        as_of: datetime,
        computed_at: datetime,
        portfolio_id: str | None,
        run_id: str,
        positions: list[FactorExposureInputPosition],
        series_by_symbol: dict[str, _SymbolSeries],
        lookback_window: int,
    ) -> FactorExposureSnapshotRecord:
        snapshot_start = perf_counter()
        benchmark_returns = series_by_symbol.get(self._config.benchmark_symbol)
        symbol_exposures: list[SymbolFactorExposure] = []
        warnings: list[str] = []

        for pos in positions:
            series = series_by_symbol.get(pos.symbol)
            exposures = self._compute_symbol_exposures(
                position=pos,
                series=series,
                benchmark_returns=benchmark_returns.returns if benchmark_returns else None,
                lookback_window=lookback_window,
            )
            for value in exposures.values():
                warnings.extend(value.warnings)
            symbol_exposures.append(
                SymbolFactorExposure(
                    symbol=pos.symbol,
                    strategy_id=pos.strategy_id,
                    sector=pos.sector,
                    weight=pos.weight,
                    exposures=exposures,
                )
            )

        strategy_exposures = self._aggregate_strategy_exposures(symbol_exposures)
        portfolio_exposure = self._aggregate_portfolio_exposure(
            portfolio_id=portfolio_id,
            symbol_exposures=symbol_exposures,
        )

        diagnostics = self._detect_concentration(
            portfolio=portfolio_exposure,
            strategy_exposures=strategy_exposures,
        )
        portfolio_exposure.concentration_diagnostics.extend(diagnostics)

        return FactorExposureSnapshotRecord(
            snapshot_id=str(uuid4()),
            run_id=run_id,
            portfolio_id=portfolio_id,
            computed_at=computed_at,
            as_of_date=as_of,
            lookback_window=lookback_window,
            benchmark_symbol=self._config.benchmark_symbol,
            benchmark_source=self._config.benchmark_source,
            factor_computation_version=FACTOR_COMPUTATION_VERSION,
            factor_methodology=self.methodology(),
            data_lineage={
                "market_data_table": "market_bars",
                "bar_interval": self._config.bar_interval,
                "price_basis": self._config.price_basis,
                "benchmark_symbol": self._config.benchmark_symbol,
                "position_count": len(symbol_exposures),
            },
            symbol_exposures=symbol_exposures,
            strategy_exposures=strategy_exposures,
            portfolio_exposure=portfolio_exposure,
            warnings=warnings,
            duration_seconds=round(perf_counter() - snapshot_start, 4),
        )

    def _compute_symbol_exposures(
        self,
        *,
        position: FactorExposureInputPosition,
        series: _SymbolSeries | None,
        benchmark_returns: list[float] | None,
        lookback_window: int,
    ) -> dict[str, FactorExposureValue]:
        if series is None:
            warning = f"Missing price history for {position.symbol}"
            return {
                name.value: FactorExposureValue(
                    factor_name=name,
                    exposure=None,
                    methodology=self.methodology()[name.value],
                    observations_used=0,
                    is_valid=False,
                    warnings=[warning],
                )
                for name in (FactorName.MARKET_BETA, FactorName.MOMENTUM, FactorName.VOLATILITY)
            } | self._metadata_factor_values(position)

        returns = series.returns[-lookback_window:]
        observations = len(returns)
        common_warnings: list[str] = []
        valid_returns = _finite_list(returns)
        if len(valid_returns) < self._config.minimum_observations_required:
            common_warnings.append(
                f"Insufficient observations for {position.symbol}: "
                f"{len(valid_returns)} < {self._config.minimum_observations_required}"
            )

        beta = self._estimate_beta(returns=returns, benchmark_returns=benchmark_returns)
        momentum = _trailing_cumulative_return(returns)
        volatility = _annualized_volatility(returns, self._config.annualization_factor)

        exposures = {
            FactorName.MARKET_BETA.value: FactorExposureValue(
                factor_name=FactorName.MARKET_BETA,
                exposure=beta,
                methodology=self.methodology()[FactorName.MARKET_BETA.value],
                observations_used=observations,
                is_valid=beta is not None,
                warnings=list(common_warnings) if beta is None else [],
            ),
            FactorName.MOMENTUM.value: FactorExposureValue(
                factor_name=FactorName.MOMENTUM,
                exposure=momentum,
                methodology=self.methodology()[FactorName.MOMENTUM.value],
                observations_used=observations,
                is_valid=momentum is not None and len(common_warnings) == 0,
                warnings=list(common_warnings) if momentum is None else [],
            ),
            FactorName.VOLATILITY.value: FactorExposureValue(
                factor_name=FactorName.VOLATILITY,
                exposure=volatility,
                methodology=self.methodology()[FactorName.VOLATILITY.value],
                observations_used=observations,
                is_valid=volatility is not None and len(common_warnings) == 0,
                warnings=list(common_warnings) if volatility is None else [],
            ),
        }
        exposures.update(self._metadata_factor_values(position))
        return exposures

    def _estimate_beta(
        self,
        *,
        returns: list[float],
        benchmark_returns: list[float] | None,
    ) -> float | None:
        if benchmark_returns is None:
            return None
        window = self._beta_window()
        r = np.array(_finite_list(returns[-window:]), dtype=float)
        b = np.array(_finite_list(benchmark_returns[-window:]), dtype=float)
        n = min(len(r), len(b))
        if n < self._config.minimum_observations_required:
            return None
        r = r[-n:]
        b = b[-n:]
        variance = float(np.var(b))
        if not math.isfinite(variance) or variance < _MIN_BENCHMARK_VARIANCE:
            return None
        beta = float(np.cov(r, b, ddof=1)[0, 1] / variance)
        if not math.isfinite(beta) or abs(beta) > _MAX_ABS_BETA:
            return None
        return round(beta, 6)

    def _metadata_factor_values(
        self, position: FactorExposureInputPosition
    ) -> dict[str, FactorExposureValue]:
        size = (
            math.log(position.market_cap)
            if position.market_cap and position.market_cap > 0
            else None
        )
        return {
            FactorName.SIZE.value: FactorExposureValue(
                factor_name=FactorName.SIZE,
                exposure=round(size, 6) if size is not None else None,
                methodology=self.methodology()[FactorName.SIZE.value],
                observations_used=1 if size is not None else 0,
                is_valid=size is not None,
                warnings=[] if size is not None else [f"Missing market_cap for {position.symbol}"],
            ),
            FactorName.QUALITY.value: FactorExposureValue(
                factor_name=FactorName.QUALITY,
                exposure=position.quality_score,
                methodology=self.methodology()[FactorName.QUALITY.value],
                observations_used=1 if position.quality_score is not None else 0,
                is_valid=position.quality_score is not None,
                warnings=[],
            ),
            FactorName.VALUE.value: FactorExposureValue(
                factor_name=FactorName.VALUE,
                exposure=position.value_score,
                methodology=self.methodology()[FactorName.VALUE.value],
                observations_used=1 if position.value_score is not None else 0,
                is_valid=position.value_score is not None,
                warnings=[],
            ),
        }

    def _aggregate_strategy_exposures(
        self, symbol_exposures: list[SymbolFactorExposure]
    ) -> list[StrategyFactorExposure]:
        by_strategy: dict[str, list[SymbolFactorExposure]] = {}
        for exposure in symbol_exposures:
            strategy_id = exposure.strategy_id or "unassigned"
            by_strategy.setdefault(strategy_id, []).append(exposure)

        results: list[StrategyFactorExposure] = []
        for strategy_id, rows in sorted(by_strategy.items()):
            total_weight = sum(abs(r.weight) for r in rows)
            factors = self._weighted_factor_values(rows)
            results.append(
                StrategyFactorExposure(
                    strategy_id=strategy_id,
                    strategy_weight=round(total_weight, 6),
                    symbol_count=len(rows),
                    exposures=factors,
                    top_symbol_contributors=self._top_symbol_contributors(rows),
                )
            )
        return results

    def _aggregate_portfolio_exposure(
        self,
        *,
        portfolio_id: str | None,
        symbol_exposures: list[SymbolFactorExposure],
    ) -> PortfolioFactorExposure:
        total_weight = sum(abs(s.weight) for s in symbol_exposures)
        strategy_ids = {s.strategy_id for s in symbol_exposures if s.strategy_id}
        sector_exposures: dict[str, float] = {}
        for exposure in symbol_exposures:
            sector = exposure.sector or "unknown"
            sector_exposures[sector] = sector_exposures.get(sector, 0.0) + abs(exposure.weight)
        if total_weight > 0:
            sector_exposures = {k: round(v / total_weight, 6) for k, v in sector_exposures.items()}

        factors = self._weighted_factor_values(symbol_exposures)
        factors[FactorName.SECTOR.value] = (
            max(sector_exposures.values()) if sector_exposures else None
        )
        return PortfolioFactorExposure(
            portfolio_id=portfolio_id,
            total_weight=round(total_weight, 6),
            symbol_count=len(symbol_exposures),
            strategy_count=len(strategy_ids),
            exposures=factors,
            sector_exposures=sector_exposures,
            concentration_diagnostics=[],
        )

    def _weighted_factor_values(self, rows: list[SymbolFactorExposure]) -> dict[str, float | None]:
        values: dict[str, float | None] = {}
        for factor in (
            FactorName.MARKET_BETA,
            FactorName.MOMENTUM,
            FactorName.VOLATILITY,
            FactorName.SIZE,
            FactorName.QUALITY,
            FactorName.VALUE,
        ):
            total_abs_weight = 0.0
            weighted_sum = 0.0
            for row in rows:
                factor_value = row.exposures.get(factor.value)
                if factor_value is None or factor_value.exposure is None:
                    continue
                w = abs(row.weight)
                weighted_sum += row.weight * factor_value.exposure
                total_abs_weight += w
            values[factor.value] = (
                round(weighted_sum / total_abs_weight, 6) if total_abs_weight else None
            )
        return values

    def _top_symbol_contributors(
        self, rows: list[SymbolFactorExposure]
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for factor in (FactorName.MARKET_BETA, FactorName.MOMENTUM, FactorName.VOLATILITY):
            contributors = []
            for row in rows:
                value = row.exposures.get(factor.value)
                if value is None or value.exposure is None:
                    continue
                contributors.append(
                    {
                        "symbol": row.symbol,
                        "weight": row.weight,
                        "factor_value": value.exposure,
                        "weighted_contribution": round(row.weight * value.exposure, 6),
                    }
                )
            contributors.sort(key=_abs_weighted_contribution, reverse=True)
            result[factor.value] = contributors[:5]
        return result

    def _detect_concentration(
        self,
        *,
        portfolio: PortfolioFactorExposure,
        strategy_exposures: list[StrategyFactorExposure],
    ) -> list[dict[str, Any]]:
        thresholds = self._config.thresholds
        alerts: list[dict[str, Any]] = []

        beta = portfolio.exposures.get(FactorName.MARKET_BETA.value)
        if (
            beta is not None
            and thresholds.max_abs_beta_exposure is not None
            and abs(beta) > thresholds.max_abs_beta_exposure
        ):
            alerts.append(
                self._alert(
                    FactorName.MARKET_BETA.value,
                    beta,
                    thresholds.max_abs_beta_exposure,
                    strategy_exposures,
                )
            )

        momentum = portfolio.exposures.get(FactorName.MOMENTUM.value)
        if (
            momentum is not None
            and thresholds.max_abs_momentum_exposure is not None
            and abs(momentum) > thresholds.max_abs_momentum_exposure
        ):
            alerts.append(
                self._alert(
                    FactorName.MOMENTUM.value,
                    momentum,
                    thresholds.max_abs_momentum_exposure,
                    strategy_exposures,
                )
            )

        volatility = portfolio.exposures.get(FactorName.VOLATILITY.value)
        if (
            volatility is not None
            and thresholds.max_volatility_tilt is not None
            and volatility > thresholds.max_volatility_tilt
        ):
            alerts.append(
                self._alert(
                    FactorName.VOLATILITY.value,
                    volatility,
                    thresholds.max_volatility_tilt,
                    strategy_exposures,
                )
            )

        sector = portfolio.exposures.get(FactorName.SECTOR.value)
        if (
            sector is not None
            and thresholds.max_sector_concentration is not None
            and sector > thresholds.max_sector_concentration
        ):
            alerts.append(
                self._alert(
                    FactorName.SECTOR.value,
                    sector,
                    thresholds.max_sector_concentration,
                    strategy_exposures,
                )
            )

        return alerts

    @staticmethod
    def _alert(
        factor_name: str,
        exposure: float,
        threshold: float,
        strategy_exposures: list[StrategyFactorExposure],
    ) -> dict[str, Any]:
        affected = [
            s.strategy_id
            for s in strategy_exposures
            if s.exposures.get(factor_name) is not None
            and abs(float(s.exposures[factor_name] or 0.0)) >= abs(threshold) * 0.5
        ]
        return {
            "event_type": FACTOR_CONCENTRATION_DETECTED,
            "factor_name": factor_name,
            "exposure": round(float(exposure), 6),
            "threshold": threshold,
            "affected_strategies": affected,
        }

    def _detect_drift(
        self,
        *,
        portfolio_id: str | None,
        lookback_window: int,
        current_exposures: dict[str, float | None],
    ) -> list[dict[str, Any]]:
        threshold = self._config.thresholds.drift_threshold
        if threshold is None:
            return []
        previous = self._repo.get_latest_portfolio_exposure(
            portfolio_id=portfolio_id, lookback_window=lookback_window
        )
        if previous is None:
            return []
        alerts: list[dict[str, Any]] = []
        for factor_name, current in current_exposures.items():
            old = previous.exposures.get(factor_name)
            if current is None or old is None:
                continue
            drift = abs(float(current) - float(old))
            if drift > threshold:
                alerts.append(
                    {
                        "event_type": FACTOR_DRIFT_DETECTED,
                        "factor_name": factor_name,
                        "previous_exposure": old,
                        "current_exposure": current,
                        "drift": round(drift, 6),
                        "threshold": threshold,
                        "lookback_window": lookback_window,
                    }
                )
        return alerts

    def _persist(self, record: FactorExposureSnapshotRecord) -> None:
        self._repo.insert_snapshot(
            FactorExposureSnapshotRow(
                snapshot_id=record.snapshot_id,
                run_id=record.run_id,
                portfolio_id=record.portfolio_id,
                computed_at=record.computed_at,
                as_of_date=record.as_of_date,
                lookback_window=record.lookback_window,
                benchmark_symbol=record.benchmark_symbol,
                benchmark_source=record.benchmark_source,
                factor_computation_version=record.factor_computation_version,
                portfolio_exposures=record.portfolio_exposure.exposures,
                strategy_exposures=[s.model_dump(mode="json") for s in record.strategy_exposures],
                symbol_exposures=[s.model_dump(mode="json") for s in record.symbol_exposures],
                sector_exposures=record.portfolio_exposure.sector_exposures,
                concentration_diagnostics=record.portfolio_exposure.concentration_diagnostics,
                warnings=record.warnings,
                factor_methodology=record.factor_methodology,
                data_lineage=record.data_lineage,
                duration_seconds=record.duration_seconds,
            )
        )
        self._repo.insert_portfolio_exposure(
            PortfolioFactorExposureRow(
                exposure_id=str(uuid4()),
                snapshot_id=record.snapshot_id,
                run_id=record.run_id,
                portfolio_id=record.portfolio_id,
                computed_at=record.computed_at,
                as_of_date=record.as_of_date,
                lookback_window=record.lookback_window,
                benchmark_symbol=record.benchmark_symbol,
                total_weight=record.portfolio_exposure.total_weight,
                symbol_count=record.portfolio_exposure.symbol_count,
                strategy_count=record.portfolio_exposure.strategy_count,
                exposures=record.portfolio_exposure.exposures,
                sector_exposures=record.portfolio_exposure.sector_exposures,
                concentration_diagnostics=record.portfolio_exposure.concentration_diagnostics,
            )
        )
        for strategy in record.strategy_exposures:
            self._repo.insert_strategy_exposure(
                StrategyFactorExposureRow(
                    exposure_id=str(uuid4()),
                    snapshot_id=record.snapshot_id,
                    run_id=record.run_id,
                    portfolio_id=record.portfolio_id,
                    strategy_id=strategy.strategy_id,
                    computed_at=record.computed_at,
                    as_of_date=record.as_of_date,
                    lookback_window=record.lookback_window,
                    benchmark_symbol=record.benchmark_symbol,
                    strategy_weight=strategy.strategy_weight,
                    symbol_count=strategy.symbol_count,
                    exposures=strategy.exposures,
                    top_symbol_contributors=strategy.top_symbol_contributors,
                )
            )

    def _emit_snapshot_events(self, record: FactorExposureSnapshotRecord) -> None:
        exposures = record.portfolio_exposure.exposures
        logger.info(
            "%s run_id=%s portfolio_id=%s window=%d benchmark=%s beta=%s momentum=%s volatility=%s alerts=%d",
            FACTOR_EXPOSURE_COMPUTED,
            record.run_id,
            record.portfolio_id,
            record.lookback_window,
            record.benchmark_symbol,
            exposures.get(FactorName.MARKET_BETA.value),
            exposures.get(FactorName.MOMENTUM.value),
            exposures.get(FactorName.VOLATILITY.value),
            len(record.portfolio_exposure.concentration_diagnostics),
        )
        for alert in record.portfolio_exposure.concentration_diagnostics:
            event_type = (
                BETA_EXPOSURE_THRESHOLD_EXCEEDED
                if alert["factor_name"] == FactorName.MARKET_BETA.value
                else FACTOR_CONCENTRATION_DETECTED
            )
            logger.warning(
                "%s run_id=%s factor=%s exposure=%.4f threshold=%.4f affected_strategies=%s benchmark=%s lookback_window=%d",
                event_type,
                record.run_id,
                alert["factor_name"],
                alert["exposure"],
                alert["threshold"],
                ",".join(alert["affected_strategies"]),
                record.benchmark_symbol,
                record.lookback_window,
            )

    def _record_snapshot_metrics(self, record: FactorExposureSnapshotRecord) -> None:
        exposures = record.portfolio_exposure.exposures
        beta = exposures.get(FactorName.MARKET_BETA.value)
        momentum = exposures.get(FactorName.MOMENTUM.value)
        volatility = exposures.get(FactorName.VOLATILITY.value)
        attrs = {
            "lookback_window": str(record.lookback_window),
            "benchmark": record.benchmark_symbol,
        }
        if beta is not None:
            ratp_portfolio_beta_exposure.record(float(beta), attrs)
        if momentum is not None:
            ratp_portfolio_momentum_exposure.record(float(momentum), attrs)
        if volatility is not None:
            ratp_portfolio_volatility_exposure.record(float(volatility), attrs)
        if record.portfolio_exposure.concentration_diagnostics:
            ratp_factor_concentration_alerts_total.add(
                len(record.portfolio_exposure.concentration_diagnostics), attrs
            )
        ratp_factor_exposure_compute_duration_seconds.record(record.duration_seconds, attrs)

    def _load_series(
        self, *, symbols: list[str], as_of: datetime, window: int
    ) -> dict[str, _SymbolSeries]:
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
            prices_by_symbol.setdefault(bar.symbol, []).append(float(bar.close))
        return {
            symbol: _SymbolSeries(
                prices=prices[-(window + 1) :], returns=_log_returns(prices[-(window + 1) :])
            )
            for symbol, prices in prices_by_symbol.items()
            if len(prices) >= 2
        }

    @staticmethod
    def _normalize_positions(
        positions: list[FactorExposureInputPosition],
    ) -> list[FactorExposureInputPosition]:
        cleaned = [p for p in positions if p.symbol and math.isfinite(p.weight) and p.weight != 0]
        total_abs_weight = sum(abs(p.weight) for p in cleaned)
        if total_abs_weight <= 0:
            return []
        return [
            p.model_copy(update={"symbol": p.symbol.upper(), "weight": p.weight / total_abs_weight})
            for p in cleaned
        ]

    def _beta_window(self) -> int:
        return self._config.beta_lookback_window or max(self._config.factor_exposure_windows)

    @staticmethod
    def methodology() -> dict[str, str]:
        return {
            FactorName.MARKET_BETA.value: "OLS beta = covariance(symbol log returns, benchmark log returns) / variance(benchmark returns) over the rolling lookback window.",
            FactorName.MOMENTUM.value: "Trailing cumulative return from rolling log returns over the lookback window.",
            FactorName.VOLATILITY.value: "Annualized realized volatility = standard deviation of rolling log returns times sqrt(annualization_factor).",
            FactorName.SECTOR.value: "Absolute portfolio weight concentration by sector; portfolio sector factor is the largest sector weight.",
            FactorName.SIZE.value: "Natural log of market capitalization when symbol metadata is supplied; aggregated by portfolio weights.",
            FactorName.QUALITY.value: "Optional quality factor metadata passthrough reserved for upstream factor feeds.",
            FactorName.VALUE.value: "Optional value factor metadata passthrough reserved for upstream factor feeds.",
        }

    @staticmethod
    def result_to_jsonable(result: FactorExposureRunResult) -> dict[str, Any]:
        return dict(result.model_dump(mode="json"))

    def get_latest_snapshot(
        self, *, portfolio_id: str | None = None, lookback_window: int | None = None
    ) -> FactorExposureSnapshotRow | None:
        return self._repo.get_latest_snapshot(
            portfolio_id=portfolio_id, lookback_window=lookback_window
        )

    def get_history(
        self,
        *,
        since: datetime,
        portfolio_id: str | None = None,
        lookback_window: int | None = None,
        limit: int = 100,
    ) -> list[FactorExposureSnapshotRow]:
        return self._repo.get_snapshot_history(
            since=since,
            portfolio_id=portfolio_id,
            lookback_window=lookback_window,
            limit=limit,
        )


def _log_returns(prices: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 > 0 and p1 > 0:
            value = math.log(p1 / p0)
            out.append(value if math.isfinite(value) else 0.0)
        else:
            out.append(0.0)
    return out


def _finite_list(values: list[float]) -> list[float]:
    return [float(v) for v in values if math.isfinite(v)]


def _abs_weighted_contribution(contributor: dict[str, Any]) -> float:
    return abs(float(contributor["weighted_contribution"]))


def _trailing_cumulative_return(returns: list[float]) -> float | None:
    finite = _finite_list(returns)
    if not finite:
        return None
    return round(math.exp(sum(finite)) - 1.0, 6)


def _annualized_volatility(returns: list[float], annualization_factor: int) -> float | None:
    finite = _finite_list(returns)
    if len(finite) < 2:
        return None
    vol = float(np.std(np.array(finite, dtype=float), ddof=1) * math.sqrt(annualization_factor))
    return round(vol, 6) if math.isfinite(vol) else None
