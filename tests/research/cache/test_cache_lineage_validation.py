"""Tests for lineage-safe cache validation."""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
)
from autonomous_trading_platform.research.cache.cache_validation import (
    LineageValidationResult,
    validate_simulation_lineage,
)


def _base_key(**overrides: Any) -> SimulationCacheKey:
    defaults: dict[str, Any] = dict(
        config_hash="cfghash",
        dataset_version="v1",
        universe_version="v1",
        price_basis="adjusted",
        symbols_hash="symhash",
        start_date="2022-01-01",
        end_date="2023-01-01",
        random_seed=42,
        stage_name="train",
        window_role="default",
        fill_policy="current_close",
        slippage_rate="0.0001",
        commission_per_share="0.0000",
        regime_dataset_version="",
        feature_versions_hash="",
    )
    defaults.update(overrides)
    return SimulationCacheKey(**defaults)


class TestLineageValidationResult:
    def test_ok_result(self):
        r = LineageValidationResult.ok()
        assert r.compatible
        assert r.reason is None

    def test_incompatible_result(self):
        r = LineageValidationResult.incompatible(CacheInvalidationReason.DATASET_VERSION_CHANGED)
        assert not r.compatible
        assert r.reason == CacheInvalidationReason.DATASET_VERSION_CHANGED

    def test_explain_ok(self):
        assert "OK" in LineageValidationResult.ok().explain()

    def test_explain_incompatible(self):
        r = LineageValidationResult.incompatible(CacheInvalidationReason.SLIPPAGE_CHANGED)
        assert "INCOMPATIBLE" in r.explain()
        assert "slippage_changed" in r.explain()


class TestValidateSimulationLineage:
    def test_identical_keys_are_compatible(self):
        key = _base_key()
        result = validate_simulation_lineage(key, key)
        assert result.compatible

    def test_same_values_different_instances_are_compatible(self):
        k1 = _base_key()
        k2 = _base_key()
        result = validate_simulation_lineage(k1, k2)
        assert result.compatible

    def test_config_hash_change_invalidates(self):
        cached = _base_key(config_hash="old")
        current = _base_key(config_hash="new")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.CONFIG_CHANGED

    def test_dataset_version_change_invalidates(self):
        cached = _base_key(dataset_version="v1")
        current = _base_key(dataset_version="v2")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.DATASET_VERSION_CHANGED

    def test_universe_version_change_invalidates(self):
        cached = _base_key(universe_version="v1")
        current = _base_key(universe_version="v2")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.UNIVERSE_VERSION_CHANGED

    def test_price_basis_change_invalidates(self):
        cached = _base_key(price_basis="adjusted")
        current = _base_key(price_basis="unadjusted")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.PRICE_BASIS_CHANGED

    def test_symbols_hash_change_invalidates(self):
        cached = _base_key(symbols_hash="hash_a")
        current = _base_key(symbols_hash="hash_b")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.SYMBOLS_CHANGED

    def test_start_date_change_invalidates(self):
        cached = _base_key(start_date="2022-01-01")
        current = _base_key(start_date="2023-01-01")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.DATE_RANGE_CHANGED

    def test_end_date_change_invalidates(self):
        cached = _base_key(end_date="2023-01-01")
        current = _base_key(end_date="2024-01-01")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.DATE_RANGE_CHANGED

    def test_seed_change_invalidates(self):
        cached = _base_key(random_seed=42)
        current = _base_key(random_seed=99)
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.SEED_CHANGED

    def test_stage_name_change_invalidates(self):
        cached = _base_key(stage_name="train")
        current = _base_key(stage_name="test")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED

    def test_window_role_change_invalidates(self):
        cached = _base_key(window_role="default")
        current = _base_key(window_role="fold_1")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED

    def test_fill_policy_change_invalidates(self):
        cached = _base_key(fill_policy="current_close")
        current = _base_key(fill_policy="next_open")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.FILL_POLICY_CHANGED

    def test_slippage_change_invalidates(self):
        cached = _base_key(slippage_rate="0.0001")
        current = _base_key(slippage_rate="0.0005")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.SLIPPAGE_CHANGED

    def test_commission_change_invalidates(self):
        cached = _base_key(commission_per_share="0.0000")
        current = _base_key(commission_per_share="0.005")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.COMMISSION_CHANGED

    def test_regime_version_change_invalidates(self):
        cached = _base_key(regime_dataset_version="")
        current = _base_key(regime_dataset_version="reg_v2")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.REGIME_VERSION_CHANGED

    def test_feature_version_change_invalidates(self):
        cached = _base_key(feature_versions_hash="")
        current = _base_key(feature_versions_hash="abcdef01")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.FEATURE_VERSION_CHANGED

    def test_all_changes_detected(self):
        fields_and_reasons = [
            ("config_hash", "newcfg", CacheInvalidationReason.CONFIG_CHANGED),
            ("dataset_version", "v99", CacheInvalidationReason.DATASET_VERSION_CHANGED),
            ("universe_version", "v99", CacheInvalidationReason.UNIVERSE_VERSION_CHANGED),
            ("price_basis", "unadjusted", CacheInvalidationReason.PRICE_BASIS_CHANGED),
            ("symbols_hash", "newsym", CacheInvalidationReason.SYMBOLS_CHANGED),
            ("start_date", "2020-01-01", CacheInvalidationReason.DATE_RANGE_CHANGED),
            ("end_date", "2025-01-01", CacheInvalidationReason.DATE_RANGE_CHANGED),
            ("random_seed", 999, CacheInvalidationReason.SEED_CHANGED),
            ("stage_name", "test", CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED),
            ("window_role", "fold_2", CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED),
            ("fill_policy", "next_open", CacheInvalidationReason.FILL_POLICY_CHANGED),
            ("slippage_rate", "0.01", CacheInvalidationReason.SLIPPAGE_CHANGED),
            ("commission_per_share", "0.01", CacheInvalidationReason.COMMISSION_CHANGED),
            ("regime_dataset_version", "rr_v1", CacheInvalidationReason.REGIME_VERSION_CHANGED),
            ("feature_versions_hash", "aabbcc", CacheInvalidationReason.FEATURE_VERSION_CHANGED),
        ]
        base = _base_key()
        for field, value, expected_reason in fields_and_reasons:
            current = _base_key(**{field: value})
            result = validate_simulation_lineage(base, current)
            assert not result.compatible, f"Expected incompatibility for field {field!r}"
            assert result.reason == expected_reason, (
                f"Field {field!r}: expected {expected_reason} got {result.reason}"
            )

    def test_stale_artifact_rejected(self):
        """Changing dataset_version means cached artifact is stale — must reject."""
        cached = _base_key(dataset_version="v1")
        current = _base_key(dataset_version="v_new_ingestion")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible

    def test_incompatible_artifacts_rejected(self):
        """Changing fill model means cached result is incompatible — must reject."""
        cached = _base_key(fill_policy="current_close")
        current = _base_key(fill_policy="next_open")
        result = validate_simulation_lineage(cached, current)
        assert not result.compatible
