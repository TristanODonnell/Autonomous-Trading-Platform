"""Tests verifying calibration_snapshot_id integration with SimulationCacheKey."""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
)


def _make_key(**overrides: Any) -> SimulationCacheKey:
    defaults: dict[str, Any] = dict(
        config_hash="cfghash",
        dataset_version="v1",
        universe_version="v1",
        price_basis="close",
        symbols_hash="symhash",
        start_date="2024-01-01",
        end_date="2024-12-31",
        random_seed=42,
        stage_name="train",
        window_role="default",
        fill_policy="current_close",
        latency_bars=0,
        cost_model_type="volume_share",
        slippage_config_hash="abc1234567890123",
        commission_per_share="0.0000",
        regime_dataset_version="",
        feature_versions_hash="",
        calibration_snapshot_id="",
    )
    defaults.update(overrides)
    return SimulationCacheKey(**defaults)


class TestCalibrationSnapshotIdInCacheKey:
    def test_default_calibration_id_is_empty(self):
        key = _make_key()
        assert key.calibration_snapshot_id == ""

    def test_different_calibration_id_changes_key_id(self):
        k1 = _make_key(calibration_snapshot_id="")
        k2 = _make_key(calibration_snapshot_id="cal-uuid-1234")
        assert k1.key_id != k2.key_id

    def test_same_calibration_id_same_key(self):
        k1 = _make_key(calibration_snapshot_id="cal-abc")
        k2 = _make_key(calibration_snapshot_id="cal-abc")
        assert k1.key_id == k2.key_id

    def test_calibration_id_in_to_dict(self):
        key = _make_key(calibration_snapshot_id="cal-xyz")
        d = key.to_dict()
        assert "calibration_snapshot_id" in d
        assert d["calibration_snapshot_id"] == "cal-xyz"

    def test_to_dict_contains_all_expected_fields(self):
        key = _make_key()
        d = key.to_dict()
        expected = {
            "adverse_threshold_bps",
            "calibration_snapshot_id",
            "config_hash",
            "dataset_version",
            "dividend_events_hash",
            "universe_version",
            "price_basis",
            "symbols_hash",
            "start_date",
            "end_date",
            "random_seed",
            "stage_name",
            "window_role",
            "fill_policy",
            "latency_bars",
            "cost_model_type",
            "slippage_config_hash",
            "commission_per_share",
            "regime_dataset_version",
            "feature_versions_hash",
            "settlement_days",
        }
        assert set(d.keys()) == expected

    def test_calibration_changed_is_a_valid_invalidation_reason(self):
        assert CacheInvalidationReason.CALIBRATION_CHANGED.value == "calibration_changed"

    def test_key_without_explicit_calibration_id_defaults_to_empty(self):
        """SimulationCacheKey backward compatibility — omitting calibration_snapshot_id is fine."""
        key = SimulationCacheKey(
            config_hash="cfghash",
            dataset_version="v1",
            universe_version="v1",
            price_basis="close",
            symbols_hash="symhash",
            start_date="2024-01-01",
            end_date="2024-12-31",
            random_seed=42,
            stage_name="train",
            window_role="default",
            fill_policy="current_close",
            latency_bars=0,
            cost_model_type="volume_share",
            slippage_config_hash="abc1234567890123",
            commission_per_share="0.0000",
            regime_dataset_version="",
            feature_versions_hash="",
        )
        assert key.calibration_snapshot_id == ""


class TestCacheKeyBuilderCalibration:
    def test_build_simulation_cache_key_includes_calibration_id(self):
        from unittest.mock import MagicMock

        from autonomous_trading_platform.research.cache.cache_key_builder import (
            build_simulation_cache_key,
        )

        mock_config = MagicMock()
        mock_config.config_hash.return_value = "cfg123"
        mock_config.type = "momentum"

        mock_run_config = MagicMock()
        mock_run_config.dataset_version = "v1"
        mock_run_config.price_basis.value = "close"
        mock_run_config.symbols = ["AAPL", "MSFT"]
        mock_run_config.start_date = "2024-01-01"
        mock_run_config.end_date = "2024-12-31"
        mock_run_config.random_seed = 42
        mock_run_config.stage_name = "train"
        mock_run_config.window_role = "default"
        mock_run_config.settlement_days = 0

        key_no_cal = build_simulation_cache_key(
            strategy_config=mock_config,
            run_config=mock_run_config,
            calibration_snapshot_id="",
        )
        key_with_cal = build_simulation_cache_key(
            strategy_config=mock_config,
            run_config=mock_run_config,
            calibration_snapshot_id="cal-uuid-abc",
        )
        assert key_no_cal.key_id != key_with_cal.key_id
        assert key_with_cal.calibration_snapshot_id == "cal-uuid-abc"
