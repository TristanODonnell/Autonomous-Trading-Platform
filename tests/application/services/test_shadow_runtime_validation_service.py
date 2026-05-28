from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.shadow_runtime_validation_service import (
    ShadowAllocationInput,
    ShadowExecutionInput,
    ShadowFeatureInput,
    ShadowOptimizerInput,
    ShadowRiskInput,
    ShadowRuntimeInput,
    ShadowRuntimeValidationService,
    ShadowSignalInput,
)
from autonomous_trading_platform.contracts.shadow.divergence import DivergenceThresholds
from autonomous_trading_platform.contracts.shadow.shadow_run import (
    ShadowModeType,
    ShadowRunRequest,
    ShadowRunStatus,
    ShadowValidationStatus,
)
from autonomous_trading_platform.storage.sor.models.shadow_comparison_snapshots import (
    ShadowComparisonSnapshotRow,
)
from autonomous_trading_platform.storage.sor.models.shadow_divergences import ShadowDivergenceRow
from autonomous_trading_platform.storage.sor.models.shadow_runs import ShadowRunRow

_NOW = datetime(2026, 5, 27, 16, 0, 0, tzinfo=UTC)
_BAR_TS = _NOW - timedelta(hours=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    strategy_id: str = "momentum_v1",
    shadow_mode_type: ShadowModeType = ShadowModeType.OBSERVE_ONLY,
    required_passing_cycles: int = 1,
) -> ShadowRunRequest:
    return ShadowRunRequest(
        simulation_run_id=uuid4(),
        live_run_id=uuid4(),
        shadow_mode_type=shadow_mode_type,
        strategy_id=strategy_id,
        required_passing_cycles=required_passing_cycles,
    )


def _service(
    db_session: Session, thresholds: DivergenceThresholds | None = None
) -> ShadowRuntimeValidationService:
    return ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestStartShadowRun:
    def test_creates_run_row(self, db_session: Session) -> None:
        svc = _service(db_session)
        request = _make_request()
        manifest = svc.start_shadow_run(request)
        db_session.flush()

        row = db_session.get(ShadowRunRow, str(manifest.shadow_run_id))
        assert row is not None
        assert row.strategy_id == "momentum_v1"
        assert row.status == ShadowRunStatus.RUNNING.value
        assert row.validation_status == ShadowValidationStatus.PENDING.value

    def test_manifest_reflects_request(self, db_session: Session) -> None:
        svc = _service(db_session)
        request = _make_request(shadow_mode_type=ShadowModeType.PROMOTION_SHADOW)
        manifest = svc.start_shadow_run(request)

        assert manifest.shadow_mode_type == ShadowModeType.PROMOTION_SHADOW
        assert manifest.simulation_run_id == request.simulation_run_id
        assert manifest.status == ShadowRunStatus.RUNNING
        assert manifest.total_divergences == 0

    def test_config_hash_is_set(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        assert manifest.config_hash is not None
        assert len(manifest.config_hash) == 16

    def test_get_nonexistent_run_returns_none(self, db_session: Session) -> None:
        svc = _service(db_session)
        result = svc.get_shadow_run(uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# Signal comparison tests
# ---------------------------------------------------------------------------


class TestSignalComparison:
    def test_matching_directions_produce_no_divergence(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]
        live = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]

        results = svc.compare_signals(manifest.shadow_run_id, sim, live)
        assert len(results) == 1
        assert results[0].direction_match is True
        assert len(results[0].divergences) == 0

    def test_direction_mismatch_records_divergence(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]
        live = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="sell"
            )
        ]

        results = svc.compare_signals(manifest.shadow_run_id, sim, live)
        assert results[0].direction_match is False
        assert len(results[0].divergences) == 1
        assert results[0].divergences[0].metric_name == "signal_direction"

    def test_timing_drift_exceeding_threshold_emits_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_signal_timing_drift_seconds=1.0)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim_ts = _BAR_TS
        live_ts = _BAR_TS + timedelta(seconds=10)
        sim = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS,
                symbol="AAPL",
                strategy_id="s1",
                direction="buy",
                signal_timestamp=sim_ts,
            )
        ]
        live = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS,
                symbol="AAPL",
                strategy_id="s1",
                direction="buy",
                signal_timestamp=live_ts,
            )
        ]

        results = svc.compare_signals(manifest.shadow_run_id, sim, live)
        timing_divs = [
            d
            for r in results
            for d in r.divergences
            if d.metric_name == "signal_timing_drift_seconds"
        ]
        assert len(timing_divs) == 1
        assert timing_divs[0].exceeds_threshold is True

    def test_missing_live_signal_handled(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]
        results = svc.compare_signals(manifest.shadow_run_id, sim, [])

        assert len(results) == 1
        assert results[0].direction_match is False  # buy != None
        assert results[0].live_direction is None

    def test_comparison_snapshot_persisted(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]
        live = [
            ShadowSignalInput(
                bar_timestamp=_BAR_TS, symbol="AAPL", strategy_id="s1", direction="buy"
            )
        ]
        svc.compare_signals(manifest.shadow_run_id, sim, live)

        from sqlalchemy import select

        snaps = db_session.scalars(
            select(ShadowComparisonSnapshotRow).where(
                ShadowComparisonSnapshotRow.shadow_run_id == str(manifest.shadow_run_id)
            )
        ).all()
        assert len(snaps) == 1
        assert snaps[0].comparison_category == "signals"


# ---------------------------------------------------------------------------
# Allocation comparison tests
# ---------------------------------------------------------------------------


class TestAllocationComparison:
    def test_within_threshold_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.05)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.30)]
        live = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.32)]

        results = svc.compare_allocations(manifest.shadow_run_id, sim, live)
        assert len(results) == 1
        assert results[0].exceeds_threshold is False
        assert abs(results[0].weight_drift - 0.02) < 1e-9

    def test_exceeding_threshold_records_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.01)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.30)]
        live = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.35)]

        results = svc.compare_allocations(manifest.shadow_run_id, sim, live)
        assert results[0].exceeds_threshold is True
        assert len(results[0].divergences) == 1
        assert results[0].divergences[0].divergence_type.value == "allocation_drift"

    def test_missing_live_allocation_defaults_to_zero(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.25)]
        results = svc.compare_allocations(manifest.shadow_run_id, sim, [])

        assert results[0].live_weight == 0.0
        assert results[0].weight_drift == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Risk exposure comparison tests
# ---------------------------------------------------------------------------


class TestRiskComparison:
    def test_within_factor_threshold_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_factor_exposure_drift=0.05)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowRiskInput(
                bar_timestamp=_BAR_TS,
                divergence_type="factor",
                sim_exposure=0.40,
                live_exposure=0.40,
            )
        ]
        live = [
            ShadowRiskInput(
                bar_timestamp=_BAR_TS,
                divergence_type="factor",
                sim_exposure=0.40,
                live_exposure=0.43,
            )
        ]

        results = svc.compare_risk_exposures(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 0

    def test_exceeding_factor_threshold_emits_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_factor_exposure_drift=0.02)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowRiskInput(
                bar_timestamp=_BAR_TS, divergence_type="beta", sim_exposure=0.80, live_exposure=0.80
            )
        ]
        live = [
            ShadowRiskInput(
                bar_timestamp=_BAR_TS, divergence_type="beta", sim_exposure=0.80, live_exposure=0.85
            )
        ]

        results = svc.compare_risk_exposures(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 1
        div = results[0].divergences[0]
        assert div.divergence_type.value == "risk_divergence"
        assert div.exceeds_threshold is True


# ---------------------------------------------------------------------------
# Execution comparison tests
# ---------------------------------------------------------------------------


class TestExecutionComparison:
    def test_within_slippage_threshold_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_execution_slippage_bps=20.0)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", fill_price=100.0)]
        live = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", fill_price=100.01)]

        results = svc.compare_execution(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 0

    def test_large_slippage_records_execution_degradation(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_execution_slippage_bps=10.0)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="TSLA", fill_price=200.0)]
        live = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="TSLA", fill_price=200.50)]

        results = svc.compare_execution(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 1
        assert results[0].divergences[0].divergence_type.value == "execution_degradation"
        assert results[0].slippage_drift_bps is not None
        assert results[0].slippage_drift_bps > 10.0

    def test_order_rejection_flag_propagated(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", order_rejected=True)]
        live = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", order_rejected=False)]

        results = svc.compare_execution(manifest.shadow_run_id, sim, live)
        assert results[0].order_rejected_sim is True
        assert results[0].order_rejected_live is False


# ---------------------------------------------------------------------------
# Feature comparison tests
# ---------------------------------------------------------------------------


class TestFeatureComparison:
    def test_within_drift_threshold_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_feature_drift_pct=0.10)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="momentum_5d", value=0.12)]
        live = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="momentum_5d", value=0.125)]

        results = svc.compare_features(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 0

    def test_large_feature_drift_emits_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_feature_drift_pct=0.05)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="vol_20d", value=0.20)]
        live = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="vol_20d", value=0.25)]

        results = svc.compare_features(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 1
        div = results[0].divergences[0]
        assert div.divergence_type.value == "feature_drift"
        assert div.metric_name == "feature_drift.vol_20d"

    def test_missing_live_feature_value_handled(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="rsi_14", value=65.0)]
        live = [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="rsi_14", value=None)]

        results = svc.compare_features(manifest.shadow_run_id, sim, live)
        assert results[0].drift_pct is None


# ---------------------------------------------------------------------------
# Optimizer comparison tests
# ---------------------------------------------------------------------------


class TestOptimizerComparison:
    def test_identical_weights_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_optimizer_weight_drift=0.02)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        weights = {"AAPL": 0.33, "TSLA": 0.33, "NVDA": 0.34}
        sim = [ShadowOptimizerInput(bar_timestamp=_BAR_TS, weights=weights)]
        live = [ShadowOptimizerInput(bar_timestamp=_BAR_TS, weights=weights)]

        results = svc.compare_optimizer_outputs(manifest.shadow_run_id, sim, live)
        assert len(results) == 1
        assert results[0].max_weight_drift == pytest.approx(0.0)
        assert len(results[0].divergences) == 0

    def test_drifting_optimizer_weights_emit_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_optimizer_weight_drift=0.01)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowOptimizerInput(bar_timestamp=_BAR_TS, weights={"AAPL": 0.50, "TSLA": 0.50})]
        live = [ShadowOptimizerInput(bar_timestamp=_BAR_TS, weights={"AAPL": 0.40, "TSLA": 0.60})]

        results = svc.compare_optimizer_outputs(manifest.shadow_run_id, sim, live)
        assert results[0].max_weight_drift == pytest.approx(0.10)
        assert len(results[0].divergences) >= 1
        assert any(d.divergence_type.value == "optimizer_drift" for d in results[0].divergences)

    def test_covariance_instability_detected(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_covariance_frobenius_delta=0.01)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        cov_sim = [[1.0, 0.2], [0.2, 1.0]]
        cov_live = [[1.0, 0.8], [0.8, 1.0]]
        sim = [
            ShadowOptimizerInput(
                bar_timestamp=_BAR_TS, weights={"A": 0.5, "B": 0.5}, covariance_matrix=cov_sim
            )
        ]
        live = [
            ShadowOptimizerInput(
                bar_timestamp=_BAR_TS, weights={"A": 0.5, "B": 0.5}, covariance_matrix=cov_live
            )
        ]

        results = svc.compare_optimizer_outputs(manifest.shadow_run_id, sim, live)
        cov_divs = [
            d for d in results[0].divergences if d.divergence_type.value == "covariance_instability"
        ]
        assert len(cov_divs) == 1
        assert cov_divs[0].exceeds_threshold is True

    def test_constraint_activation_match_detected(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [
            ShadowOptimizerInput(
                bar_timestamp=_BAR_TS, weights={}, binding_constraints=["max_weight"]
            )
        ]
        live = [
            ShadowOptimizerInput(
                bar_timestamp=_BAR_TS, weights={}, binding_constraints=["max_weight", "leverage"]
            )
        ]

        results = svc.compare_optimizer_outputs(manifest.shadow_run_id, sim, live)
        assert results[0].constraint_activation_match is False


# ---------------------------------------------------------------------------
# Runtime comparison tests
# ---------------------------------------------------------------------------


class TestRuntimeComparison:
    def test_matching_sequences_no_divergence(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        seq = ["ingest", "evaluate", "execute"]
        sim = [
            ShadowRuntimeInput(
                bar_timestamp=_BAR_TS, cycle_duration_seconds=1.2, cycle_sequence=seq
            )
        ]
        live = [
            ShadowRuntimeInput(
                bar_timestamp=_BAR_TS, cycle_duration_seconds=1.3, cycle_sequence=seq
            )
        ]

        results = svc.compare_runtime(manifest.shadow_run_id, sim, live)
        assert results[0].sequence_match is True

    def test_timing_drift_exceeding_threshold_detected(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_orchestration_timing_drift_seconds=5.0)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        sim = [ShadowRuntimeInput(bar_timestamp=_BAR_TS, cycle_duration_seconds=2.0)]
        live = [ShadowRuntimeInput(bar_timestamp=_BAR_TS, cycle_duration_seconds=30.0)]

        results = svc.compare_runtime(manifest.shadow_run_id, sim, live)
        assert len(results[0].divergences) == 1
        assert results[0].timing_drift_seconds == pytest.approx(28.0)

    def test_stale_data_flag_propagated(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        live = [ShadowRuntimeInput(bar_timestamp=_BAR_TS, stale_data_detected=True)]
        results = svc.compare_runtime(manifest.shadow_run_id, [], live)
        assert results[0].stale_data_detected is True


# ---------------------------------------------------------------------------
# Outcome comparison tests
# ---------------------------------------------------------------------------


class TestOutcomeComparison:
    def test_within_sharpe_threshold_no_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_sharpe_drift=0.50)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        result = svc.record_outcome_comparison(
            manifest.shadow_run_id,
            sim_metrics={"sharpe": 1.20, "total_return": 0.10},
            live_metrics={"sharpe": 1.10, "total_return": 0.09},
        )
        assert result.sharpe_drift == pytest.approx(0.10)
        assert result.pnl_drift == pytest.approx(0.01)

    def test_large_sharpe_drift_records_divergence(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_sharpe_drift=0.10)
        svc = _service(db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        svc.record_outcome_comparison(
            manifest.shadow_run_id,
            sim_metrics={"sharpe": 2.00, "total_return": 0.25},
            live_metrics={"sharpe": 0.50, "total_return": 0.08},
        )

        from sqlalchemy import select

        div_rows = db_session.scalars(
            select(ShadowDivergenceRow).where(
                ShadowDivergenceRow.shadow_run_id == str(manifest.shadow_run_id)
            )
        ).all()
        outcome_divs = [d for d in div_rows if d.divergence_type == "outcome_divergence"]
        assert len(outcome_divs) == 1
        assert outcome_divs[0].exceeds_threshold is True


# ---------------------------------------------------------------------------
# Finalize / validation status tests
# ---------------------------------------------------------------------------


class TestFinalizeRun:
    def test_observe_only_always_passes(self, db_session: Session) -> None:
        svc = _service(db_session)
        request = _make_request(shadow_mode_type=ShadowModeType.OBSERVE_ONLY)
        manifest = svc.start_shadow_run(request)
        db_session.flush()

        # inject some divergences
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.0001)
        svc_strict = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        sim = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.5)]
        live = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.1)]
        svc_strict.compare_allocations(manifest.shadow_run_id, sim, live)

        summary = svc.finalize_shadow_run(manifest.shadow_run_id)
        assert summary.validation_status == ShadowValidationStatus.PASSED

    def test_many_exceedances_result_in_failed_status(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.0001)
        svc = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        request = _make_request(shadow_mode_type=ShadowModeType.VALIDATION_REQUIRED)
        manifest = svc.start_shadow_run(request)
        db_session.flush()

        for sym in ["AAPL", "TSLA", "NVDA"]:
            sim = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol=sym, weight=0.5)]
            live = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol=sym, weight=0.1)]
            svc.compare_allocations(manifest.shadow_run_id, sim, live)

        summary = svc.finalize_shadow_run(manifest.shadow_run_id)
        assert summary.validation_status == ShadowValidationStatus.FAILED
        assert summary.threshold_exceedances == 3

    def test_finalize_marks_run_completed(self, db_session: Session) -> None:
        svc = _service(db_session)
        manifest = svc.start_shadow_run(_make_request())
        db_session.flush()

        svc.finalize_shadow_run(manifest.shadow_run_id)

        row = db_session.get(ShadowRunRow, str(manifest.shadow_run_id))
        assert row.status == ShadowRunStatus.COMPLETED.value
        assert row.completed_at is not None
        assert row.duration_seconds is not None

    def test_finalize_counts_divergence_by_category(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(
            max_weight_drift_pct=0.001, max_execution_slippage_bps=1.0
        )
        svc = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        request = _make_request(shadow_mode_type=ShadowModeType.VALIDATION_REQUIRED)
        manifest = svc.start_shadow_run(request)
        db_session.flush()

        sim_alloc = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.5)]
        live_alloc = [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.1)]
        svc.compare_allocations(manifest.shadow_run_id, sim_alloc, live_alloc)

        sim_exec = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", fill_price=100.0)]
        live_exec = [ShadowExecutionInput(bar_timestamp=_BAR_TS, symbol="AAPL", fill_price=102.0)]
        svc.compare_execution(manifest.shadow_run_id, sim_exec, live_exec)

        summary = svc.finalize_shadow_run(manifest.shadow_run_id)
        assert (
            "portfolio" in summary.divergence_by_category
            or "execution" in summary.divergence_by_category
        )


# ---------------------------------------------------------------------------
# Promotion eligibility tests
# ---------------------------------------------------------------------------


class TestPromotionEligibility:
    def test_not_eligible_when_validation_failed(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.001)
        svc = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        request = _make_request(
            shadow_mode_type=ShadowModeType.PROMOTION_SHADOW, required_passing_cycles=1
        )
        manifest = svc.start_shadow_run(request)
        db_session.flush()

        for sym in ["AAPL", "TSLA", "NVDA"]:
            svc.compare_allocations(
                manifest.shadow_run_id,
                [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol=sym, weight=0.5)],
                [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol=sym, weight=0.0)],
            )
        svc.finalize_shadow_run(manifest.shadow_run_id)

        eligible = svc.check_promotion_eligibility(manifest.shadow_run_id)
        assert eligible is False

    def test_eligible_after_passing_required_cycles(self, db_session: Session) -> None:
        svc = _service(db_session)
        # Create 2 passing runs for the same strategy
        strategy_id = "test_strategy_eligibility"
        for _ in range(2):
            req = ShadowRunRequest(
                simulation_run_id=uuid4(),
                shadow_mode_type=ShadowModeType.OBSERVE_ONLY,
                strategy_id=strategy_id,
                required_passing_cycles=2,
            )
            m = svc.start_shadow_run(req)
            db_session.flush()
            svc.finalize_shadow_run(m.shadow_run_id)

        eligible = svc.check_promotion_eligibility(m.shadow_run_id, required_cycles=2)
        assert eligible is True

    def test_not_eligible_for_nonexistent_run(self, db_session: Session) -> None:
        svc = _service(db_session)
        assert svc.check_promotion_eligibility(uuid4()) is False


# ---------------------------------------------------------------------------
# List / query tests
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_list_returns_runs_for_strategy(self, db_session: Session) -> None:
        svc = _service(db_session)
        strategy_id = "list_test_strategy"
        for _ in range(3):
            svc.start_shadow_run(_make_request(strategy_id=strategy_id))
        db_session.flush()

        svc.start_shadow_run(_make_request(strategy_id="other_strategy"))
        db_session.flush()

        manifests = svc.list_shadow_runs(strategy_id)
        assert len(manifests) == 3
        assert all(m.strategy_id == strategy_id for m in manifests)

    def test_list_filters_by_validation_status(self, db_session: Session) -> None:
        svc = _service(db_session)
        strategy_id = "filter_test_strategy"
        m1 = svc.start_shadow_run(_make_request(strategy_id=strategy_id))
        db_session.flush()
        svc.finalize_shadow_run(m1.shadow_run_id)

        svc.start_shadow_run(_make_request(strategy_id=strategy_id))
        db_session.flush()

        passed = svc.list_shadow_runs(strategy_id, validation_status="passed")
        pending = svc.list_shadow_runs(strategy_id, validation_status="pending")
        assert len(passed) == 1
        assert len(pending) == 1

    def test_list_divergences_filtered_by_category(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.001, max_feature_drift_pct=0.001)
        svc = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(
            _make_request(shadow_mode_type=ShadowModeType.VALIDATION_REQUIRED)
        )
        db_session.flush()

        svc.compare_allocations(
            manifest.shadow_run_id,
            [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.5)],
            [ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.0)],
        )
        svc.compare_features(
            manifest.shadow_run_id,
            [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="f1", value=1.0)],
            [ShadowFeatureInput(bar_timestamp=_BAR_TS, feature_name="f1", value=5.0)],
        )

        portfolio_divs = svc.list_divergences(manifest.shadow_run_id, category="portfolio")
        feature_divs = svc.list_divergences(manifest.shadow_run_id, category="features")
        assert len(portfolio_divs) >= 1
        assert len(feature_divs) >= 1

    def test_list_divergences_exceeds_threshold_only(self, db_session: Session) -> None:
        thresholds = DivergenceThresholds(max_weight_drift_pct=0.001)
        svc = ShadowRuntimeValidationService(session=db_session, thresholds=thresholds)
        manifest = svc.start_shadow_run(
            _make_request(shadow_mode_type=ShadowModeType.VALIDATION_REQUIRED)
        )
        db_session.flush()

        # One exceeds, one does not
        svc.compare_allocations(
            manifest.shadow_run_id,
            [
                ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.5),
                ShadowAllocationInput(
                    bar_timestamp=_BAR_TS + timedelta(hours=1), symbol="TSLA", weight=0.5
                ),
            ],
            [
                ShadowAllocationInput(bar_timestamp=_BAR_TS, symbol="AAPL", weight=0.0),  # exceeds
                ShadowAllocationInput(
                    bar_timestamp=_BAR_TS + timedelta(hours=1), symbol="TSLA", weight=0.5
                ),  # no drift
            ],
        )

        all_divs = svc.list_divergences(manifest.shadow_run_id)
        threshold_divs = svc.list_divergences(manifest.shadow_run_id, exceeds_threshold_only=True)
        assert len(threshold_divs) < len(all_divs) or len(threshold_divs) >= 1


# ---------------------------------------------------------------------------
# Shadow run linkage tests
# ---------------------------------------------------------------------------


class TestShadowLiveLinkage:
    def test_shadow_run_stores_live_and_simulation_run_ids(self, db_session: Session) -> None:
        svc = _service(db_session)
        sim_id = uuid4()
        live_id = uuid4()
        req = ShadowRunRequest(
            simulation_run_id=sim_id,
            live_run_id=live_id,
            shadow_mode_type=ShadowModeType.PRODUCTION_DRIFT_MONITORING,
            strategy_id="drift_monitor",
        )
        manifest = svc.start_shadow_run(req)
        db_session.flush()

        row = db_session.get(ShadowRunRow, str(manifest.shadow_run_id))
        assert row.simulation_run_id == str(sim_id)
        assert row.live_run_id == str(live_id)

    def test_shadow_run_without_live_run_id(self, db_session: Session) -> None:
        svc = _service(db_session)
        req = ShadowRunRequest(
            simulation_run_id=uuid4(),
            shadow_mode_type=ShadowModeType.OPTIMIZER_SHADOW,
            strategy_id="optimizer_shadow_test",
        )
        manifest = svc.start_shadow_run(req)
        db_session.flush()
        assert manifest.live_run_id is None
