from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.shadow.comparison_results import (
    AllocationComparisonResult,
    ExecutionComparisonResult,
    FeatureComparisonResult,
    OptimizerComparisonResult,
    OutcomeComparisonResult,
    RiskComparisonResult,
    RuntimeComparisonResult,
    SignalComparisonResult,
)
from autonomous_trading_platform.contracts.shadow.divergence import (
    DivergenceCategory,
    DivergenceRecord,
    DivergenceThresholds,
    DivergenceType,
)
from autonomous_trading_platform.contracts.shadow.shadow_run import (
    ShadowModeType,
    ShadowRunManifest,
    ShadowRunRequest,
    ShadowRunStatus,
    ShadowValidationStatus,
)
from autonomous_trading_platform.contracts.shadow.shadow_validation_summary import (
    ShadowValidationSummary,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_shadow_divergence_total,
    ratp_shadow_execution_slippage_drift,
    ratp_shadow_factor_exposure_drift,
    ratp_shadow_optimizer_divergence,
    ratp_shadow_runs_total,
    ratp_shadow_threshold_exceedances_total,
    ratp_shadow_validation_duration_seconds,
    ratp_shadow_weight_drift,
)
from autonomous_trading_platform.storage.sor.models.shadow_comparison_snapshots import (
    ShadowComparisonSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.shadow_divergences import (
    ShadowDivergenceRow,
)
from autonomous_trading_platform.storage.sor.models.shadow_runs import ShadowRunRow
from autonomous_trading_platform.storage.sor.repositories.core.shadow_comparison_snapshot_repository import (
    ShadowComparisonSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.shadow_divergence_repository import (
    ShadowDivergenceRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.shadow_run_repository import (
    ShadowRunRepository,
)

logger = get_logger(__name__)

# Structured log event names
SHADOW_RUN_STARTED = "SHADOW_RUN_STARTED"
SHADOW_RUN_COMPLETED = "SHADOW_RUN_COMPLETED"
SHADOW_DIVERGENCE_DETECTED = "SHADOW_DIVERGENCE_DETECTED"
SHADOW_VALIDATION_FAILED = "SHADOW_VALIDATION_FAILED"
OPTIMIZER_DRIFT_DETECTED = "OPTIMIZER_DRIFT_DETECTED"
FEATURE_DRIFT_DETECTED = "FEATURE_DRIFT_DETECTED"
EXECUTION_DEGRADATION_DETECTED = "EXECUTION_DEGRADATION_DETECTED"

_BPS = 10_000.0  # basis points divisor for price-to-bps conversion


@dataclass(frozen=True)
class ShadowSignalInput:
    bar_timestamp: datetime
    symbol: str
    strategy_id: str
    direction: str | None = None
    confidence: float | None = None
    signal_timestamp: datetime | None = None


@dataclass(frozen=True)
class ShadowAllocationInput:
    bar_timestamp: datetime
    symbol: str
    weight: float


@dataclass(frozen=True)
class ShadowRiskInput:
    bar_timestamp: datetime
    divergence_type: str
    sim_exposure: float | None = None
    live_exposure: float | None = None
    symbol: str | None = None
    sector: str | None = None
    factor: str | None = None


@dataclass(frozen=True)
class ShadowExecutionInput:
    bar_timestamp: datetime
    symbol: str
    fill_price: float | None = None
    quantity: float | None = None
    latency_ms: float | None = None
    order_rejected: bool = False


@dataclass(frozen=True)
class ShadowFeatureInput:
    bar_timestamp: datetime
    feature_name: str
    value: float | None = None
    freshness_seconds: float | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class ShadowOptimizerInput:
    bar_timestamp: datetime
    weights: dict[str, float] = field(default_factory=dict)
    objective_value: float | None = None
    expected_volatility: float | None = None
    binding_constraints: list[str] = field(default_factory=list)
    covariance_matrix: list[list[float]] | None = None


@dataclass(frozen=True)
class ShadowRuntimeInput:
    bar_timestamp: datetime
    cycle_duration_seconds: float | None = None
    cycle_sequence: list[str] = field(default_factory=list)
    stale_data_detected: bool = False


class ShadowRuntimeValidationService:
    """Orchestrates shadow-mode validation: runs simulation side-by-side with
    live/paper execution, compares behavior across 7 dimensions, classifies
    divergence, and generates governance-ready validation artifacts.
    """

    def __init__(
        self,
        *,
        session: Session,
        thresholds: DivergenceThresholds | None = None,
        shadow_run_repo: ShadowRunRepository | None = None,
        divergence_repo: ShadowDivergenceRepository | None = None,
        snapshot_repo: ShadowComparisonSnapshotRepository | None = None,
    ) -> None:
        self._session = session
        self._thresholds = thresholds or DivergenceThresholds()
        self._run_repo = shadow_run_repo or ShadowRunRepository(session)
        self._div_repo = divergence_repo or ShadowDivergenceRepository(session)
        self._snap_repo = snapshot_repo or ShadowComparisonSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_shadow_run(self, request: ShadowRunRequest) -> ShadowRunManifest:
        shadow_run_id = request.shadow_run_id or uuid4()
        now = datetime.now(UTC)
        config_hash = self._hash_config(request)

        row = ShadowRunRow(
            shadow_run_id=str(shadow_run_id),
            simulation_run_id=str(request.simulation_run_id),
            live_run_id=str(request.live_run_id) if request.live_run_id else None,
            strategy_id=request.strategy_id,
            shadow_mode_type=request.shadow_mode_type.value,
            status=ShadowRunStatus.RUNNING.value,
            validation_status=ShadowValidationStatus.PENDING.value,
            total_divergences=0,
            threshold_exceedances=0,
            divergence_by_category={},
            promotion_eligible=False,
            required_passing_cycles=request.required_passing_cycles,
            config_hash=config_hash,
            covariance_snapshot_ref=request.covariance_snapshot_ref,
            factor_snapshot_ref=request.factor_snapshot_ref,
            allocation_config_hash=request.allocation_config_hash,
            optimizer_run_ids=request.optimizer_run_ids,
            started_at=now,
            completed_at=None,
            duration_seconds=None,
            metadata_json=request.metadata or {},
        )
        self._run_repo.insert(row)

        logger.info(
            "shadow_runtime_validation.started",
            extra={
                "event_type": SHADOW_RUN_STARTED,
                "shadow_run_id": str(shadow_run_id),
                "strategy_id": request.strategy_id,
                "shadow_mode_type": request.shadow_mode_type.value,
                "simulation_run_id": str(request.simulation_run_id),
                "live_run_id": str(request.live_run_id) if request.live_run_id else None,
            },
        )
        ratp_shadow_runs_total.add(
            1,
            attributes={
                "shadow_mode_type": request.shadow_mode_type.value,
                "strategy_id": request.strategy_id,
            },
        )

        return self._manifest_from_row(row)

    def finalize_shadow_run(self, shadow_run_id: UUID) -> ShadowValidationSummary:
        t0 = perf_counter()
        run_id_str = str(shadow_run_id)
        row = self._run_repo.get(run_id_str)
        if row is None:
            raise ValueError(f"Shadow run {shadow_run_id} not found")

        div_rows = self._div_repo.list_for_run(run_id_str, limit=10000)
        total = len(div_rows)
        exceedances = sum(1 for r in div_rows if r.exceeds_threshold)

        by_cat: dict[str, int] = {}
        for d in div_rows:
            by_cat[d.category] = by_cat.get(d.category, 0) + 1

        validation_status = self._determine_validation_status(
            exceedances=exceedances,
            shadow_mode_type=row.shadow_mode_type,
        )
        passing_cycles = self._run_repo.count_passing_cycles(row.strategy_id)
        promotion_eligible = (
            validation_status == ShadowValidationStatus.PASSED
            and passing_cycles >= row.required_passing_cycles
        )

        now = datetime.now(UTC)
        duration = perf_counter() - t0

        row.status = ShadowRunStatus.COMPLETED.value
        row.validation_status = validation_status.value
        row.total_divergences = total
        row.threshold_exceedances = exceedances
        row.divergence_by_category = by_cat
        row.promotion_eligible = promotion_eligible
        row.completed_at = now
        row.duration_seconds = duration
        self._run_repo.update(row)

        snap_rows = self._snap_repo.list_for_run(run_id_str, limit=5000)
        (
            signal_results,
            alloc_results,
            risk_results,
            exec_results,
            feat_results,
            opt_results,
            runtime_results,
        ) = self._hydrate_comparison_results(snap_rows)

        logger.info(
            "shadow_runtime_validation.completed",
            extra={
                "event_type": SHADOW_RUN_COMPLETED,
                "shadow_run_id": run_id_str,
                "strategy_id": row.strategy_id,
                "validation_status": validation_status.value,
                "total_divergences": total,
                "threshold_exceedances": exceedances,
                "promotion_eligible": promotion_eligible,
                "duration_seconds": round(duration, 3),
            },
        )
        ratp_shadow_validation_duration_seconds.record(
            duration,
            attributes={
                "strategy_id": row.strategy_id,
                "validation_status": validation_status.value,
            },
        )

        if validation_status == ShadowValidationStatus.FAILED:
            logger.warning(
                "shadow_runtime_validation.failed",
                extra={
                    "event_type": SHADOW_VALIDATION_FAILED,
                    "shadow_run_id": run_id_str,
                    "strategy_id": row.strategy_id,
                    "threshold_exceedances": exceedances,
                },
            )

        return ShadowValidationSummary(
            shadow_run_id=UUID(run_id_str),
            live_run_id=UUID(row.live_run_id) if row.live_run_id else None,
            simulation_run_id=UUID(row.simulation_run_id),
            shadow_mode_type=ShadowModeType(row.shadow_mode_type),
            strategy_id=row.strategy_id,
            validation_status=validation_status,
            total_divergences=total,
            threshold_exceedances=exceedances,
            promotion_eligible=promotion_eligible,
            divergence_by_category=by_cat,
            signal_comparison=signal_results,
            allocation_comparison=alloc_results,
            risk_comparison=risk_results,
            execution_comparison=exec_results,
            feature_comparison=feat_results,
            optimizer_comparison=opt_results,
            runtime_comparison=runtime_results,
            started_at=row.started_at,
            completed_at=now,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Comparison methods
    # ------------------------------------------------------------------

    def compare_signals(
        self,
        shadow_run_id: UUID,
        sim_signals: list[ShadowSignalInput],
        live_signals: list[ShadowSignalInput],
    ) -> list[SignalComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {(s.bar_timestamp, s.symbol, s.strategy_id): s for s in sim_signals}
        live_index = {(s.bar_timestamp, s.symbol, s.strategy_id): s for s in live_signals}
        all_keys = set(sim_index) | set(live_index)

        results: list[SignalComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for key in sorted(all_keys):
            bar_ts, symbol, strategy_id = key
            sim = sim_index.get(key)
            live = live_index.get(key)

            sim_dir = sim.direction if sim else None
            live_dir = live.direction if live else None
            direction_match = sim_dir == live_dir

            confidence_drift = None
            if (
                sim is not None
                and live is not None
                and sim.confidence is not None
                and live.confidence is not None
            ):
                confidence_drift = abs(sim.confidence - live.confidence)

            timing_drift = None
            if (
                sim is not None
                and live is not None
                and sim.signal_timestamp is not None
                and live.signal_timestamp is not None
            ):
                timing_drift = abs((sim.signal_timestamp - live.signal_timestamp).total_seconds())

            divs: list[DivergenceRecord] = []

            if not direction_match:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.SIGNAL_DRIFT,
                    category=DivergenceCategory.SIGNALS,
                    metric_name="signal_direction",
                    sim_value=None,
                    live_value=None,
                    divergence_magnitude=1.0,
                    threshold=0.0,
                    symbol=symbol,
                    strategy_id=strategy_id,
                    details={"sim_direction": sim_dir, "live_direction": live_dir},
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))

            if (
                timing_drift is not None
                and timing_drift > self._thresholds.max_signal_timing_drift_seconds
            ):
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.ORCHESTRATION_TIMING_DRIFT,
                    category=DivergenceCategory.SIGNALS,
                    metric_name="signal_timing_drift_seconds",
                    sim_value=0.0,
                    live_value=timing_drift,
                    divergence_magnitude=timing_drift,
                    threshold=self._thresholds.max_signal_timing_drift_seconds,
                    symbol=symbol,
                    strategy_id=strategy_id,
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))
                logger.info(
                    "shadow_runtime_validation.signal_timing_drift",
                    extra={
                        "event_type": SHADOW_DIVERGENCE_DETECTED,
                        "shadow_run_id": run_id_str,
                        "symbol": symbol,
                        "timing_drift_seconds": timing_drift,
                    },
                )

            result = SignalComparisonResult(
                shadow_run_id=shadow_run_id,
                bar_timestamp=bar_ts,
                symbol=symbol,
                strategy_id=strategy_id,
                direction_match=direction_match,
                sim_direction=sim_dir,
                live_direction=live_dir,
                sim_confidence=sim.confidence if sim else None,
                live_confidence=live.confidence if live else None,
                confidence_drift=confidence_drift,
                timing_drift_seconds=timing_drift,
                divergences=divs,
            )
            results.append(result)

            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.SIGNALS.value,
                    sim_values={
                        "direction": sim_dir,
                        "confidence": sim.confidence if sim else None,
                    },
                    live_values={
                        "direction": live_dir,
                        "confidence": live.confidence if live else None,
                    },
                    drift_metrics={
                        "direction_match": direction_match,
                        "timing_drift_seconds": timing_drift,
                    },
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_allocations(
        self,
        shadow_run_id: UUID,
        sim_allocations: list[ShadowAllocationInput],
        live_allocations: list[ShadowAllocationInput],
    ) -> list[AllocationComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {(a.bar_timestamp, a.symbol): a for a in sim_allocations}
        live_index = {(a.bar_timestamp, a.symbol): a for a in live_allocations}
        all_keys = set(sim_index) | set(live_index)

        results: list[AllocationComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for key in sorted(all_keys):
            bar_ts, symbol = key
            sim = sim_index.get(key)
            live = live_index.get(key)

            sim_w = sim.weight if sim else 0.0
            live_w = live.weight if live else 0.0
            drift = abs(sim_w - live_w)
            exceeds = drift > self._thresholds.max_weight_drift_pct

            divs: list[DivergenceRecord] = []
            if exceeds:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.ALLOCATION_DRIFT,
                    category=DivergenceCategory.PORTFOLIO,
                    metric_name="portfolio_weight_drift",
                    sim_value=sim_w,
                    live_value=live_w,
                    divergence_magnitude=drift,
                    threshold=self._thresholds.max_weight_drift_pct,
                    symbol=symbol,
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))

            ratp_shadow_weight_drift.record(
                drift,
                attributes={"symbol": symbol},
            )

            results.append(
                AllocationComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    symbol=symbol,
                    sim_weight=sim_w,
                    live_weight=live_w,
                    weight_drift=drift,
                    exceeds_threshold=exceeds,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.PORTFOLIO.value,
                    sim_values={"weight": sim_w},
                    live_values={"weight": live_w},
                    drift_metrics={"weight_drift": drift, "exceeds_threshold": exceeds},
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_risk_exposures(
        self,
        shadow_run_id: UUID,
        sim_risk: list[ShadowRiskInput],
        live_risk: list[ShadowRiskInput],
    ) -> list[RiskComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {
            (r.bar_timestamp, r.divergence_type, r.symbol, r.sector, r.factor): r for r in sim_risk
        }
        live_index = {
            (r.bar_timestamp, r.divergence_type, r.symbol, r.sector, r.factor): r for r in live_risk
        }
        all_keys = set(sim_index) | set(live_index)

        results: list[RiskComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for key in sorted(all_keys):
            bar_ts, dtype, symbol, sector, factor = key
            sim = sim_index.get(key)
            live = live_index.get(key)

            sim_exp = sim.sim_exposure if sim else None
            live_exp = live.live_exposure if live else None
            drift = None
            if sim_exp is not None and live_exp is not None:
                drift = abs(sim_exp - live_exp)

            divs: list[DivergenceRecord] = []
            if drift is not None and drift > self._thresholds.max_factor_exposure_drift:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.RISK_DIVERGENCE,
                    category=DivergenceCategory.RISK,
                    metric_name=f"risk_exposure_drift.{dtype}",
                    sim_value=sim_exp,
                    live_value=live_exp,
                    divergence_magnitude=drift,
                    threshold=self._thresholds.max_factor_exposure_drift,
                    symbol=symbol,
                    details={"divergence_type": dtype, "sector": sector, "factor": factor},
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))
                ratp_shadow_factor_exposure_drift.record(
                    drift,
                    attributes={"divergence_type": dtype},
                )

            results.append(
                RiskComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    divergence_type=dtype,
                    sim_exposure=sim_exp,
                    live_exposure=live_exp,
                    exposure_drift=drift,
                    symbol=symbol,
                    sector=sector,
                    factor=factor,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.RISK.value,
                    sim_values={"exposure": sim_exp},
                    live_values={"exposure": live_exp},
                    drift_metrics={"drift": drift, "type": dtype},
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_execution(
        self,
        shadow_run_id: UUID,
        sim_fills: list[ShadowExecutionInput],
        live_fills: list[ShadowExecutionInput],
    ) -> list[ExecutionComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {(f.bar_timestamp, f.symbol): f for f in sim_fills}
        live_index = {(f.bar_timestamp, f.symbol): f for f in live_fills}
        all_keys = set(sim_index) | set(live_index)

        results: list[ExecutionComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for key in sorted(all_keys):
            bar_ts, symbol = key
            sim = sim_index.get(key)
            live = live_index.get(key)

            sim_price = sim.fill_price if sim else None
            live_price = live.fill_price if live else None

            slippage_drift_bps = None
            if sim_price is not None and live_price is not None and sim_price > 0:
                slippage_drift_bps = abs(sim_price - live_price) / sim_price * _BPS

            latency_drift = None
            if (
                sim is not None
                and live is not None
                and sim.latency_ms is not None
                and live.latency_ms is not None
            ):
                latency_drift = abs(sim.latency_ms - live.latency_ms)

            divs: list[DivergenceRecord] = []
            if (
                slippage_drift_bps is not None
                and slippage_drift_bps > self._thresholds.max_execution_slippage_bps
            ):
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.EXECUTION_DEGRADATION,
                    category=DivergenceCategory.EXECUTION,
                    metric_name="execution_slippage_drift_bps",
                    sim_value=0.0,
                    live_value=slippage_drift_bps,
                    divergence_magnitude=slippage_drift_bps,
                    threshold=self._thresholds.max_execution_slippage_bps,
                    symbol=symbol,
                    details={"sim_price": sim_price, "live_price": live_price},
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))
                logger.warning(
                    "shadow_runtime_validation.execution_degradation",
                    extra={
                        "event_type": EXECUTION_DEGRADATION_DETECTED,
                        "shadow_run_id": run_id_str,
                        "symbol": symbol,
                        "slippage_drift_bps": slippage_drift_bps,
                    },
                )
                ratp_shadow_execution_slippage_drift.record(
                    slippage_drift_bps,
                    attributes={"symbol": symbol},
                )

            results.append(
                ExecutionComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    symbol=symbol,
                    sim_fill_price=sim_price,
                    live_fill_price=live_price,
                    slippage_drift_bps=slippage_drift_bps,
                    latency_drift_ms=latency_drift,
                    sim_qty=sim.quantity if sim else None,
                    live_qty=live.quantity if live else None,
                    order_rejected_sim=sim.order_rejected if sim else False,
                    order_rejected_live=live.order_rejected if live else False,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.EXECUTION.value,
                    sim_values={"fill_price": sim_price, "qty": sim.quantity if sim else None},
                    live_values={"fill_price": live_price, "qty": live.quantity if live else None},
                    drift_metrics={
                        "slippage_drift_bps": slippage_drift_bps,
                        "latency_drift_ms": latency_drift,
                    },
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_features(
        self,
        shadow_run_id: UUID,
        sim_features: list[ShadowFeatureInput],
        live_features: list[ShadowFeatureInput],
    ) -> list[FeatureComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {(f.bar_timestamp, f.feature_name, f.symbol): f for f in sim_features}
        live_index = {(f.bar_timestamp, f.feature_name, f.symbol): f for f in live_features}
        all_keys = set(sim_index) | set(live_index)

        results: list[FeatureComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for key in sorted(all_keys):
            bar_ts, feature_name, symbol = key
            sim = sim_index.get(key)
            live = live_index.get(key)

            sim_val = sim.value if sim else None
            live_val = live.value if live else None

            drift_pct = None
            if sim_val is not None and live_val is not None and abs(sim_val) > 1e-9:
                drift_pct = abs(sim_val - live_val) / abs(sim_val)

            freshness_match = True
            if (
                sim is not None
                and live is not None
                and sim.freshness_seconds is not None
                and live.freshness_seconds is not None
            ):
                freshness_match = abs(sim.freshness_seconds - live.freshness_seconds) < 60.0

            divs: list[DivergenceRecord] = []
            if drift_pct is not None and drift_pct > self._thresholds.max_feature_drift_pct:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.FEATURE_DRIFT,
                    category=DivergenceCategory.FEATURES,
                    metric_name=f"feature_drift.{feature_name}",
                    sim_value=sim_val,
                    live_value=live_val,
                    divergence_magnitude=drift_pct,
                    threshold=self._thresholds.max_feature_drift_pct,
                    symbol=symbol,
                    details={"feature_name": feature_name, "drift_pct": drift_pct},
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))
                logger.info(
                    "shadow_runtime_validation.feature_drift",
                    extra={
                        "event_type": FEATURE_DRIFT_DETECTED,
                        "shadow_run_id": run_id_str,
                        "feature_name": feature_name,
                        "symbol": symbol,
                        "drift_pct": drift_pct,
                    },
                )

            results.append(
                FeatureComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    feature_name=feature_name,
                    symbol=symbol,
                    sim_value=sim_val,
                    live_value=live_val,
                    drift_pct=drift_pct,
                    freshness_match=freshness_match,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.FEATURES.value,
                    sim_values={
                        "value": sim_val,
                        "freshness_seconds": sim.freshness_seconds if sim else None,
                    },
                    live_values={
                        "value": live_val,
                        "freshness_seconds": live.freshness_seconds if live else None,
                    },
                    drift_metrics={"drift_pct": drift_pct, "freshness_match": freshness_match},
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_optimizer_outputs(
        self,
        shadow_run_id: UUID,
        sim_optimizer: list[ShadowOptimizerInput],
        live_optimizer: list[ShadowOptimizerInput],
    ) -> list[OptimizerComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {o.bar_timestamp: o for o in sim_optimizer}
        live_index = {o.bar_timestamp: o for o in live_optimizer}
        all_ts = sorted(set(sim_index) | set(live_index))

        results: list[OptimizerComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for bar_ts in all_ts:
            sim = sim_index.get(bar_ts)
            live = live_index.get(bar_ts)

            sim_w = sim.weights if sim else {}
            live_w = live.weights if live else {}

            all_syms = set(sim_w) | set(live_w)
            max_weight_drift = 0.0
            for sym in all_syms:
                drift = abs(sim_w.get(sym, 0.0) - live_w.get(sym, 0.0))
                if drift > max_weight_drift:
                    max_weight_drift = drift

            sim_constraints = set(sim.binding_constraints) if sim else set()
            live_constraints = set(live.binding_constraints) if live else set()
            constraint_match = sim_constraints == live_constraints

            frob = None
            if (
                sim is not None
                and live is not None
                and sim.covariance_matrix is not None
                and live.covariance_matrix is not None
            ):
                frob = self._frobenius_delta(sim.covariance_matrix, live.covariance_matrix)

            divs: list[DivergenceRecord] = []
            if max_weight_drift > self._thresholds.max_optimizer_weight_drift:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.OPTIMIZER_DRIFT,
                    category=DivergenceCategory.OPTIMIZER,
                    metric_name="optimizer_weight_drift",
                    sim_value=None,
                    live_value=max_weight_drift,
                    divergence_magnitude=max_weight_drift,
                    threshold=self._thresholds.max_optimizer_weight_drift,
                    details={"sim_weights": sim_w, "live_weights": live_w},
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))
                logger.warning(
                    "shadow_runtime_validation.optimizer_drift",
                    extra={
                        "event_type": OPTIMIZER_DRIFT_DETECTED,
                        "shadow_run_id": run_id_str,
                        "max_weight_drift": max_weight_drift,
                    },
                )
                ratp_shadow_optimizer_divergence.record(
                    max_weight_drift,
                    attributes={"shadow_run_id": run_id_str},
                )

            if frob is not None and frob > self._thresholds.max_covariance_frobenius_delta:
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.COVARIANCE_INSTABILITY,
                    category=DivergenceCategory.OPTIMIZER,
                    metric_name="covariance_frobenius_delta",
                    sim_value=0.0,
                    live_value=frob,
                    divergence_magnitude=frob,
                    threshold=self._thresholds.max_covariance_frobenius_delta,
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))

            results.append(
                OptimizerComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    sim_weights=sim_w,
                    live_weights=live_w,
                    max_weight_drift=max_weight_drift,
                    covariance_frobenius_delta=frob,
                    constraint_activation_match=constraint_match,
                    sim_objective_value=sim.objective_value if sim else None,
                    live_objective_value=live.objective_value if live else None,
                    sim_expected_volatility=sim.expected_volatility if sim else None,
                    live_expected_volatility=live.expected_volatility if live else None,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.OPTIMIZER.value,
                    sim_values={
                        "weights": sim_w,
                        "objective_value": sim.objective_value if sim else None,
                    },
                    live_values={
                        "weights": live_w,
                        "objective_value": live.objective_value if live else None,
                    },
                    drift_metrics={
                        "max_weight_drift": max_weight_drift,
                        "frob_delta": frob,
                        "constraint_match": constraint_match,
                    },
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def compare_runtime(
        self,
        shadow_run_id: UUID,
        sim_runtime: list[ShadowRuntimeInput],
        live_runtime: list[ShadowRuntimeInput],
    ) -> list[RuntimeComparisonResult]:
        run_id_str = str(shadow_run_id)
        sim_index = {r.bar_timestamp: r for r in sim_runtime}
        live_index = {r.bar_timestamp: r for r in live_runtime}
        all_ts = sorted(set(sim_index) | set(live_index))

        results: list[RuntimeComparisonResult] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        snap_rows: list[ShadowComparisonSnapshotRow] = []

        for bar_ts in all_ts:
            sim = sim_index.get(bar_ts)
            live = live_index.get(bar_ts)

            sim_dur = sim.cycle_duration_seconds if sim else None
            live_dur = live.cycle_duration_seconds if live else None
            timing_drift = None
            if sim_dur is not None and live_dur is not None:
                timing_drift = abs(sim_dur - live_dur)

            sim_seq = sim.cycle_sequence if sim else []
            live_seq = live.cycle_sequence if live else []
            sequence_match = sim_seq == live_seq

            stale = (sim.stale_data_detected if sim else False) or (
                live.stale_data_detected if live else False
            )
            sync_mismatch = not sequence_match

            divs: list[DivergenceRecord] = []
            if (
                timing_drift is not None
                and timing_drift > self._thresholds.max_orchestration_timing_drift_seconds
            ):
                div = self._make_divergence(
                    shadow_run_id=shadow_run_id,
                    timestamp=bar_ts,
                    divergence_type=DivergenceType.ORCHESTRATION_TIMING_DRIFT,
                    category=DivergenceCategory.RUNTIME,
                    metric_name="orchestration_timing_drift_seconds",
                    sim_value=sim_dur,
                    live_value=live_dur,
                    divergence_magnitude=timing_drift,
                    threshold=self._thresholds.max_orchestration_timing_drift_seconds,
                )
                divs.append(div)
                divergence_rows.append(self._div_record_to_row(div))

            results.append(
                RuntimeComparisonResult(
                    shadow_run_id=shadow_run_id,
                    bar_timestamp=bar_ts,
                    sequence_match=sequence_match,
                    timing_drift_seconds=timing_drift,
                    sim_cycle_duration_seconds=sim_dur,
                    live_cycle_duration_seconds=live_dur,
                    stale_data_detected=stale,
                    sync_mismatch=sync_mismatch,
                    divergences=divs,
                )
            )
            snap_rows.append(
                self._make_snapshot(
                    shadow_run_id=run_id_str,
                    bar_ts=bar_ts,
                    category=DivergenceCategory.RUNTIME.value,
                    sim_values={"cycle_duration_seconds": sim_dur, "cycle_sequence": sim_seq},
                    live_values={"cycle_duration_seconds": live_dur, "cycle_sequence": live_seq},
                    drift_metrics={
                        "timing_drift_seconds": timing_drift,
                        "sequence_match": sequence_match,
                        "stale": stale,
                    },
                )
            )

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)
        if snap_rows:
            self._snap_repo.insert_many(snap_rows)

        return results

    def record_outcome_comparison(
        self,
        shadow_run_id: UUID,
        sim_metrics: dict[str, float | None],
        live_metrics: dict[str, float | None],
    ) -> OutcomeComparisonResult:
        run_id_str = str(shadow_run_id)

        sim_sharpe = sim_metrics.get("sharpe")
        live_sharpe = live_metrics.get("sharpe")
        sharpe_drift = None
        if sim_sharpe is not None and live_sharpe is not None:
            sharpe_drift = abs(sim_sharpe - live_sharpe)

        pnl_drift = None
        sim_ret = sim_metrics.get("total_return")
        live_ret = live_metrics.get("total_return")
        if sim_ret is not None and live_ret is not None:
            pnl_drift = abs(sim_ret - live_ret)

        divs: list[DivergenceRecord] = []
        divergence_rows: list[ShadowDivergenceRow] = []
        now = datetime.now(UTC)

        if sharpe_drift is not None and sharpe_drift > self._thresholds.max_sharpe_drift:
            div = self._make_divergence(
                shadow_run_id=shadow_run_id,
                timestamp=now,
                divergence_type=DivergenceType.OUTCOME_DIVERGENCE,
                category=DivergenceCategory.OUTCOMES,
                metric_name="sharpe_drift",
                sim_value=sim_sharpe,
                live_value=live_sharpe,
                divergence_magnitude=sharpe_drift,
                threshold=self._thresholds.max_sharpe_drift,
            )
            divs.append(div)
            divergence_rows.append(self._div_record_to_row(div))

        if divergence_rows:
            self._div_repo.insert_many(divergence_rows)

        self._snap_repo.insert(
            self._make_snapshot(
                shadow_run_id=run_id_str,
                bar_ts=now,
                category=DivergenceCategory.OUTCOMES.value,
                sim_values={k: v for k, v in sim_metrics.items()},
                live_values={k: v for k, v in live_metrics.items()},
                drift_metrics={"sharpe_drift": sharpe_drift, "pnl_drift": pnl_drift},
            )
        )

        return OutcomeComparisonResult(
            shadow_run_id=shadow_run_id,
            sim_total_return=sim_ret,
            live_total_return=live_ret,
            sim_sharpe=sim_sharpe,
            live_sharpe=live_sharpe,
            sim_max_drawdown=sim_metrics.get("max_drawdown"),
            live_max_drawdown=live_metrics.get("max_drawdown"),
            sim_volatility=sim_metrics.get("volatility"),
            live_volatility=live_metrics.get("volatility"),
            sim_turnover=sim_metrics.get("turnover"),
            live_turnover=live_metrics.get("turnover"),
            pnl_drift=pnl_drift,
            sharpe_drift=sharpe_drift,
        )

    # ------------------------------------------------------------------
    # Query / governance
    # ------------------------------------------------------------------

    def get_shadow_run(self, shadow_run_id: UUID) -> ShadowRunManifest | None:
        row = self._run_repo.get(str(shadow_run_id))
        if row is None:
            return None
        return self._manifest_from_row(row)

    def list_shadow_runs(
        self,
        strategy_id: str,
        *,
        status: str | None = None,
        validation_status: str | None = None,
        limit: int = 50,
    ) -> list[ShadowRunManifest]:
        rows = self._run_repo.list_for_strategy(
            strategy_id,
            status=status,
            validation_status=validation_status,
            limit=limit,
        )
        return [self._manifest_from_row(r) for r in rows]

    def list_divergences(
        self,
        shadow_run_id: UUID,
        *,
        category: str | None = None,
        exceeds_threshold_only: bool = False,
        limit: int = 500,
    ) -> list[ShadowDivergenceRow]:
        return self._div_repo.list_for_run(
            str(shadow_run_id),
            category=category,
            exceeds_threshold_only=exceeds_threshold_only,
            limit=limit,
        )

    def check_promotion_eligibility(
        self, shadow_run_id: UUID, required_cycles: int | None = None
    ) -> bool:
        row = self._run_repo.get(str(shadow_run_id))
        if row is None:
            return False
        if row.validation_status != ShadowValidationStatus.PASSED.value:
            return False
        cycles_needed = (
            required_cycles if required_cycles is not None else row.required_passing_cycles
        )
        passing = self._run_repo.count_passing_cycles(row.strategy_id)
        return passing >= cycles_needed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _determine_validation_status(
        self,
        *,
        exceedances: int,
        shadow_mode_type: str,
    ) -> ShadowValidationStatus:
        if shadow_mode_type == ShadowModeType.OBSERVE_ONLY:
            return ShadowValidationStatus.PASSED
        if exceedances == 0:
            return ShadowValidationStatus.PASSED
        if exceedances <= 2:
            return ShadowValidationStatus.DEGRADED
        return ShadowValidationStatus.FAILED

    def _make_divergence(
        self,
        *,
        shadow_run_id: UUID,
        timestamp: datetime,
        divergence_type: DivergenceType,
        category: DivergenceCategory,
        metric_name: str,
        divergence_magnitude: float,
        threshold: float,
        sim_value: float | None = None,
        live_value: float | None = None,
        symbol: str | None = None,
        strategy_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DivergenceRecord:
        exceeds = divergence_magnitude > threshold
        if exceeds:
            ratp_shadow_divergence_total.add(
                1,
                attributes={
                    "divergence_type": divergence_type.value,
                    "category": category.value,
                },
            )
            ratp_shadow_threshold_exceedances_total.add(
                1,
                attributes={"category": category.value},
            )
        return DivergenceRecord(
            divergence_id=uuid4(),
            shadow_run_id=shadow_run_id,
            timestamp=timestamp,
            divergence_type=divergence_type,
            category=category,
            metric_name=metric_name,
            divergence_magnitude=divergence_magnitude,
            threshold=threshold,
            exceeds_threshold=exceeds,
            sim_value=sim_value,
            live_value=live_value,
            symbol=symbol,
            strategy_id=strategy_id,
            details=details or {},
        )

    def _div_record_to_row(self, div: DivergenceRecord) -> ShadowDivergenceRow:
        return ShadowDivergenceRow(
            divergence_id=str(div.divergence_id),
            shadow_run_id=str(div.shadow_run_id),
            timestamp=div.timestamp,
            divergence_type=div.divergence_type.value,
            category=div.category.value,
            metric_name=div.metric_name,
            symbol=div.symbol,
            strategy_id=div.strategy_id,
            sim_value=div.sim_value,
            live_value=div.live_value,
            divergence_magnitude=div.divergence_magnitude,
            threshold=div.threshold,
            exceeds_threshold=div.exceeds_threshold,
            details=div.details,
        )

    def _make_snapshot(
        self,
        *,
        shadow_run_id: str,
        bar_ts: datetime,
        category: str,
        sim_values: dict[str, Any],
        live_values: dict[str, Any],
        drift_metrics: dict[str, Any],
    ) -> ShadowComparisonSnapshotRow:
        return ShadowComparisonSnapshotRow(
            snapshot_id=str(uuid4()),
            shadow_run_id=shadow_run_id,
            bar_timestamp=bar_ts,
            comparison_category=category,
            sim_values=sim_values,
            live_values=live_values,
            drift_metrics=drift_metrics,
            metadata_json={},
        )

    def _manifest_from_row(self, row: ShadowRunRow) -> ShadowRunManifest:
        return ShadowRunManifest(
            shadow_run_id=UUID(row.shadow_run_id),
            live_run_id=UUID(row.live_run_id) if row.live_run_id else None,
            simulation_run_id=UUID(row.simulation_run_id),
            shadow_mode_type=ShadowModeType(row.shadow_mode_type),
            strategy_id=row.strategy_id,
            status=ShadowRunStatus(row.status),
            validation_status=ShadowValidationStatus(row.validation_status),
            total_divergences=row.total_divergences,
            threshold_exceedances=row.threshold_exceedances,
            promotion_eligible=row.promotion_eligible,
            config_hash=row.config_hash,
            covariance_snapshot_ref=row.covariance_snapshot_ref,
            factor_snapshot_ref=row.factor_snapshot_ref,
            allocation_config_hash=row.allocation_config_hash,
            optimizer_run_ids=row.optimizer_run_ids or [],
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_seconds=row.duration_seconds,
            metadata=row.metadata_json or {},
        )

    @staticmethod
    def _hash_config(request: ShadowRunRequest) -> str:
        payload = {
            "simulation_run_id": str(request.simulation_run_id),
            "shadow_mode_type": request.shadow_mode_type.value,
            "strategy_id": request.strategy_id,
            "allocation_config_hash": request.allocation_config_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    @staticmethod
    def _frobenius_delta(a: list[list[float]], b: list[list[float]]) -> float:
        if len(a) != len(b) or not a:
            return float("inf")
        total = 0.0
        for row_a, row_b in zip(a, b, strict=False):
            if len(row_a) != len(row_b):
                return float("inf")
            for va, vb in zip(row_a, row_b, strict=False):
                total += (va - vb) ** 2
        return float(total**0.5)

    @staticmethod
    def _hydrate_comparison_results(
        snap_rows: list[ShadowComparisonSnapshotRow],
    ) -> tuple[
        list[SignalComparisonResult],
        list[AllocationComparisonResult],
        list[RiskComparisonResult],
        list[ExecutionComparisonResult],
        list[FeatureComparisonResult],
        list[OptimizerComparisonResult],
        list[RuntimeComparisonResult],
    ]:
        # Return empty lists — full hydration requires rejoining with divergence rows
        # which callers can do if needed; finalize() provides counts, not full re-hydration
        return [], [], [], [], [], [], []
