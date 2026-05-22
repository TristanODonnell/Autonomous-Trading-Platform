"""Tests for cache key identity — determinism, stability, and collision-safety."""

from __future__ import annotations

from typing import Any

import pytest

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
    StrategyGenerationCacheKey,
)

# ---------------------------------------------------------------------------
# StrategyGenerationCacheKey
# ---------------------------------------------------------------------------


class TestStrategyGenerationCacheKey:
    def test_key_id_equals_config_hash(self):
        key = StrategyGenerationCacheKey(config_hash="abc123", strategy_type="momentum")
        assert key.key_id == "abc123"

    def test_empty_config_hash_raises(self):
        with pytest.raises(ValueError, match="config_hash"):
            StrategyGenerationCacheKey(config_hash="", strategy_type="momentum")

    def test_empty_strategy_type_raises(self):
        with pytest.raises(ValueError, match="strategy_type"):
            StrategyGenerationCacheKey(config_hash="abc", strategy_type="")

    def test_to_dict_roundtrip(self):
        key = StrategyGenerationCacheKey(config_hash="deadbeef", strategy_type="macd")
        d = key.to_dict()
        assert d["config_hash"] == "deadbeef"
        assert d["strategy_type"] == "macd"

    def test_frozen(self):
        key = StrategyGenerationCacheKey(config_hash="abc", strategy_type="momentum")
        with pytest.raises(AttributeError):
            key.config_hash = "other"  # type: ignore[misc]

    def test_equality(self):
        k1 = StrategyGenerationCacheKey(config_hash="abc", strategy_type="momentum")
        k2 = StrategyGenerationCacheKey(config_hash="abc", strategy_type="momentum")
        assert k1 == k2

    def test_different_type_same_hash_not_equal(self):
        k1 = StrategyGenerationCacheKey(config_hash="abc", strategy_type="momentum")
        k2 = StrategyGenerationCacheKey(config_hash="abc", strategy_type="macd")
        assert k1 != k2


# ---------------------------------------------------------------------------
# SimulationCacheKey
# ---------------------------------------------------------------------------


def _make_sim_key(**overrides: Any) -> SimulationCacheKey:
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
        slippage_rate="0.0001",
        commission_per_share="0.0000",
        regime_dataset_version="",
        feature_versions_hash="",
    )
    defaults.update(overrides)
    return SimulationCacheKey(**defaults)


class TestSimulationCacheKey:
    def test_key_id_is_deterministic(self):
        k1 = _make_sim_key()
        k2 = _make_sim_key()
        assert k1.key_id == k2.key_id

    def test_key_id_is_sha256_hex(self):
        key = _make_sim_key()
        assert len(key.key_id) == 64
        int(key.key_id, 16)  # must be valid hex

    def test_key_id_changes_with_config_hash(self):
        k1 = _make_sim_key(config_hash="aaa")
        k2 = _make_sim_key(config_hash="bbb")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_dataset_version(self):
        k1 = _make_sim_key(dataset_version="v1")
        k2 = _make_sim_key(dataset_version="v2")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_slippage(self):
        k1 = _make_sim_key(slippage_rate="0.0001")
        k2 = _make_sim_key(slippage_rate="0.0005")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_fill_policy(self):
        k1 = _make_sim_key(fill_policy="current_close")
        k2 = _make_sim_key(fill_policy="next_open")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_commission(self):
        k1 = _make_sim_key(commission_per_share="0.0000")
        k2 = _make_sim_key(commission_per_share="0.005")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_seed(self):
        k1 = _make_sim_key(random_seed=42)
        k2 = _make_sim_key(random_seed=99)
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_universe_version(self):
        k1 = _make_sim_key(universe_version="v1")
        k2 = _make_sim_key(universe_version="v2")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_regime_version(self):
        k1 = _make_sim_key(regime_dataset_version="")
        k2 = _make_sim_key(regime_dataset_version="reg_v2")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_feature_versions(self):
        k1 = _make_sim_key(feature_versions_hash="")
        k2 = _make_sim_key(feature_versions_hash="abcdef01")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_stage_name(self):
        k1 = _make_sim_key(stage_name="train")
        k2 = _make_sim_key(stage_name="test")
        assert k1.key_id != k2.key_id

    def test_key_id_changes_with_window_role(self):
        k1 = _make_sim_key(window_role="default")
        k2 = _make_sim_key(window_role="fold_1")
        assert k1.key_id != k2.key_id

    def test_empty_config_hash_raises(self):
        with pytest.raises(ValueError, match="config_hash"):
            _make_sim_key(config_hash="")

    def test_empty_dataset_version_raises(self):
        with pytest.raises(ValueError, match="dataset_version"):
            _make_sim_key(dataset_version="")

    def test_to_dict_contains_all_fields(self):
        key = _make_sim_key()
        d = key.to_dict()
        expected_fields = {
            "config_hash",
            "dataset_version",
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
            "slippage_rate",
            "commission_per_share",
            "regime_dataset_version",
            "feature_versions_hash",
        }
        assert set(d.keys()) == expected_fields

    def test_frozen(self):
        key = _make_sim_key()
        with pytest.raises(AttributeError):
            key.config_hash = "other"  # type: ignore[misc]


class TestCacheInvalidationReason:
    def test_all_reasons_are_strings(self):
        for reason in CacheInvalidationReason:
            assert isinstance(reason.value, str)

    def test_not_found_is_default(self):
        assert CacheInvalidationReason.NOT_FOUND.value == "not_found"
