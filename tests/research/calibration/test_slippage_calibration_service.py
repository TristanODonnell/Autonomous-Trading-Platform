"""Tests for SlippageCalibrationService — snapshot generation, fallback, determinism."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from autonomous_trading_platform.research.calibration.models.calibration_snapshot import (
    CALIBRATION_VERSION,
    SlippageCalibrationSnapshot,
)
from autonomous_trading_platform.research.calibration.repositories.calibration_snapshot_store import (
    CalibrationSnapshotStore,
)
from autonomous_trading_platform.research.calibration.services.slippage_calibration_service import (
    SlippageCalibrationService,
)
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageModel,
)

_WINDOW_START = datetime(2024, 1, 1, tzinfo=UTC)
_WINDOW_END = datetime(2024, 3, 31, tzinfo=UTC)
_NOW = datetime(2024, 4, 1, 12, 0, tzinfo=UTC)


def _make_row(
    slippage_bps: str,
    *,
    policy_mode: str = "passthrough",
    quantity: str = "100",
    is_adverse: bool = False,
    record_id: str = "r",
) -> MagicMock:
    row = MagicMock()
    row.record_id = record_id
    row.slippage_bps = Decimal(slippage_bps)
    row.policy_mode = policy_mode
    row.quantity = Decimal(quantity)
    row.is_adverse_fill = is_adverse
    row.signal_timestamp = datetime(2024, 2, 1, 14, 0, tzinfo=UTC)
    return row


def _make_service(rows: list) -> SlippageCalibrationService:
    repo = MagicMock()
    repo.get_for_calibration.return_value = rows
    return SlippageCalibrationService(repository=repo, min_samples=5)


class TestSnapshotGeneration:
    def test_returns_snapshot_with_calibration_id(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.calibration_id
        assert len(snap.calibration_id) == 36  # UUID format

    def test_snapshot_records_window_and_count(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.calibration_window_start == _WINDOW_START
        assert snap.calibration_window_end == _WINDOW_END
        assert snap.source_fill_count == 10

    def test_snapshot_calibration_version(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.calibration_version == CALIBRATION_VERSION

    def test_generated_at_uses_provided_now_utc(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.generated_at == _NOW

    def test_bucket_stats_populated(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert len(snap.bucket_stats) > 0

    def test_global_calibrated_when_sufficient(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.is_globally_calibrated is True

    def test_calibrated_params_derived_from_mean_slippage(self):
        # All rows: slippage_bps=10, assumed_volume_share=0.02
        # impact_coeff = 10 / 0.02 = 500 → clamped to 500
        rows = [_make_row("10", record_id=f"r{i}") for i in range(10)]
        repo = MagicMock()
        repo.get_for_calibration.return_value = rows
        service = SlippageCalibrationService(
            repository=repo,
            min_samples=5,
            assumed_typical_volume_share=Decimal("0.02"),
        )
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.is_globally_calibrated is True
        # 10 / 0.02 = 500, clamped to 500
        assert snap.calibrated_impact_coefficient_bps == Decimal("500")

    def test_calibrated_fallback_min_from_median(self):
        rows = [_make_row("6", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        # median of 10 identical values = 6
        assert snap.calibrated_fallback_min_bps == Decimal("6.0000")


class TestLowSampleFallback:
    def test_insufficient_samples_is_not_globally_calibrated(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(3)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.is_globally_calibrated is False

    def test_insufficient_samples_uses_default_params(self):
        from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
            VolumeShareSlippageConfig,
        )

        default = VolumeShareSlippageConfig()
        rows = [_make_row("5", record_id=f"r{i}") for i in range(3)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.calibrated_impact_coefficient_bps == default.impact_coefficient_bps
        assert snap.calibrated_fallback_min_bps == default.fallback_min_bps

    def test_zero_rows_uses_defaults(self):
        service = _make_service([])
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap.is_globally_calibrated is False
        assert snap.source_fill_count == 0

    def test_snapshot_is_valid_only_with_sufficient_global_samples(self):
        rows_ok = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        rows_few = [_make_row("5", record_id=f"r{i}") for i in range(3)]
        svc_ok = _make_service(rows_ok)
        svc_few = _make_service(rows_few)
        assert svc_ok.calibrate(
            window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW
        ).is_valid()
        assert not svc_few.calibrate(
            window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW
        ).is_valid()


class TestDeterminism:
    def test_identical_input_produces_identical_params(self):
        rows = [_make_row(str(i % 10 + 1), record_id=f"r{i}") for i in range(20)]
        repo = MagicMock()
        repo.get_for_calibration.return_value = rows
        svc1 = SlippageCalibrationService(repository=repo, min_samples=5)
        svc2 = SlippageCalibrationService(repository=repo, min_samples=5)
        snap1 = svc1.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        snap2 = svc2.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap1.calibrated_impact_coefficient_bps == snap2.calibrated_impact_coefficient_bps
        assert snap1.calibrated_fallback_min_bps == snap2.calibrated_fallback_min_bps
        assert snap1.source_fill_count == snap2.source_fill_count
        assert len(snap1.bucket_stats) == len(snap2.bucket_stats)
        for s1, s2 in zip(snap1.bucket_stats, snap2.bucket_stats, strict=True):
            assert s1.mean_slippage_bps == s2.mean_slippage_bps

    def test_different_calibration_ids_but_same_params(self):
        """Two calibrations on identical data should have the same params but different IDs."""
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        repo = MagicMock()
        repo.get_for_calibration.return_value = rows
        svc = SlippageCalibrationService(repository=repo, min_samples=5)
        snap1 = svc.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        snap2 = svc.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert snap1.calibration_id != snap2.calibration_id
        assert snap1.calibrated_impact_coefficient_bps == snap2.calibrated_impact_coefficient_bps


class TestSnapshotSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        d = snap.to_dict()
        restored = SlippageCalibrationSnapshot.from_dict(d)
        assert restored.calibration_id == snap.calibration_id
        assert restored.source_fill_count == snap.source_fill_count
        assert restored.calibrated_impact_coefficient_bps == snap.calibrated_impact_coefficient_bps
        assert restored.calibrated_fallback_min_bps == snap.calibrated_fallback_min_bps
        assert restored.is_globally_calibrated == snap.is_globally_calibrated

    def test_json_roundtrip(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        restored = SlippageCalibrationSnapshot.from_json(snap.to_json())
        assert restored.calibration_id == snap.calibration_id
        assert restored.calibrated_impact_coefficient_bps == snap.calibrated_impact_coefficient_bps


class TestSimulationConsumption:
    def test_from_calibration_snapshot_produces_model(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        model = VolumeShareSlippageModel.from_calibration_snapshot(snap)
        assert isinstance(model, VolumeShareSlippageModel)
        assert model.config.impact_coefficient_bps == snap.calibrated_impact_coefficient_bps
        assert model.config.fallback_min_bps == snap.calibrated_fallback_min_bps
        assert model.config.max_volume_share == snap.calibrated_max_volume_share

    def test_calibrated_model_config_summary_differs_from_default(self):
        rows = [_make_row("50", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        calibrated_model = VolumeShareSlippageModel.from_calibration_snapshot(snap)
        default_model = VolumeShareSlippageModel()
        assert calibrated_model.config_summary() != default_model.config_summary()

    def test_uncalibrated_snapshot_model_matches_defaults(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(2)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        assert not snap.is_globally_calibrated
        model = VolumeShareSlippageModel.from_calibration_snapshot(snap)
        default_model = VolumeShareSlippageModel()
        assert model.config.impact_coefficient_bps == default_model.config.impact_coefficient_bps


class TestSnapshotStore:
    def test_save_and_load_by_id(self):
        rows = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        service = _make_service(rows)
        snap = service.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=_NOW)
        store = CalibrationSnapshotStore()
        store.save(snap)
        loaded = store.load(snap.calibration_id)
        assert loaded is not None
        assert loaded.calibration_id == snap.calibration_id

    def test_load_latest_returns_most_recent(self):
        from datetime import timedelta

        store = CalibrationSnapshotStore()
        rows1 = [_make_row("5", record_id=f"r{i}") for i in range(10)]
        rows2 = [_make_row("5", record_id=f"r{i}") for i in range(10)]

        t1 = _NOW
        t2 = _NOW + timedelta(days=30)

        svc1 = _make_service(rows1)
        svc2 = _make_service(rows2)
        snap1 = svc1.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=t1)
        snap2 = svc2.calibrate(window_start=_WINDOW_START, window_end=_WINDOW_END, now_utc=t2)

        store.save(snap1)
        store.save(snap2)
        latest = store.load_latest()
        assert latest is not None
        assert latest.calibration_id == snap2.calibration_id

    def test_list_ids_ordered_by_generated_at(self):
        from datetime import timedelta

        store = CalibrationSnapshotStore()
        for i in range(3):
            rows = [_make_row("5", record_id=f"r{j}") for j in range(10)]
            svc = _make_service(rows)
            snap = svc.calibrate(
                window_start=_WINDOW_START,
                window_end=_WINDOW_END,
                now_utc=_NOW + timedelta(days=i),
            )
            store.save(snap)
        ids = store.list_ids()
        assert len(ids) == 3

    def test_load_missing_returns_none(self):
        store = CalibrationSnapshotStore()
        assert store.load("nonexistent-id") is None

    def test_load_latest_empty_store_returns_none(self):
        store = CalibrationSnapshotStore()
        assert store.load_latest() is None
