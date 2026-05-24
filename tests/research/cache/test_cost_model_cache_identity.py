"""Tests for cost model cache identity — changing model type or config invalidates the key."""

from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.research.cache.cache_identity import CacheInvalidationReason
from autonomous_trading_platform.research.cache.cache_key_builder import (
    _hash_slippage_config,
    build_simulation_cache_key,
)
from autonomous_trading_platform.research.cache.cache_validation import validate_simulation_lineage
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.models.spread_aware_slippage_model import (
    SpreadAwareSlippageConfig,
    SpreadAwareSlippageModel,
)
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageConfig,
    VolumeShareSlippageModel,
)
from tests.research.cache.test_simulation_result_cache import (
    _make_run_config,
    _make_strategy_config,
)


class TestSlippageConfigHash:
    def test_fixed_rate_hash_is_deterministic(self):
        m = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.0001")))
        assert _hash_slippage_config(m) == _hash_slippage_config(m)

    def test_different_fixed_rates_produce_different_hash(self):
        m1 = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.0001")))
        m2 = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.001")))
        assert _hash_slippage_config(m1) != _hash_slippage_config(m2)

    def test_different_model_types_produce_different_hash(self):
        fixed = SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0.0001")))
        vs = VolumeShareSlippageModel()
        spread = SpreadAwareSlippageModel()
        assert _hash_slippage_config(fixed) != _hash_slippage_config(vs)
        assert _hash_slippage_config(vs) != _hash_slippage_config(spread)
        assert _hash_slippage_config(fixed) != _hash_slippage_config(spread)

    def test_volume_share_hash_changes_with_impact_coefficient(self):
        m1 = VolumeShareSlippageModel(
            VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("25"))
        )
        m2 = VolumeShareSlippageModel(
            VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("50"))
        )
        assert _hash_slippage_config(m1) != _hash_slippage_config(m2)

    def test_volume_share_hash_changes_with_max_volume_share(self):
        m1 = VolumeShareSlippageModel(VolumeShareSlippageConfig(max_volume_share=Decimal("0.05")))
        m2 = VolumeShareSlippageModel(VolumeShareSlippageConfig(max_volume_share=Decimal("0.15")))
        assert _hash_slippage_config(m1) != _hash_slippage_config(m2)

    def test_spread_aware_hash_changes_with_min_spread(self):
        m1 = SpreadAwareSlippageModel(SpreadAwareSlippageConfig(min_half_spread_bps=Decimal("3")))
        m2 = SpreadAwareSlippageModel(SpreadAwareSlippageConfig(min_half_spread_bps=Decimal("10")))
        assert _hash_slippage_config(m1) != _hash_slippage_config(m2)

    def test_hash_length_is_16_chars(self):
        m = VolumeShareSlippageModel()
        h = _hash_slippage_config(m)
        assert len(h) == 16


class TestCacheKeySlippageFields:
    def test_cost_model_type_in_cache_key(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k_fixed = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=SlippageModel(SlippageModelConfig()),
        )
        k_vs = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(),
        )
        assert k_fixed.cost_model_type == "fixed_bps"
        assert k_vs.cost_model_type == "volume_share"

    def test_changing_cost_model_type_changes_key_id(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=SlippageModel(SlippageModelConfig()),
        )
        k2 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(),
        )
        assert k1.key_id != k2.key_id

    def test_changing_slippage_coefficient_changes_key_id(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(
                VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("25"))
            ),
        )
        k2 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(
                VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("50"))
            ),
        )
        assert k1.key_id != k2.key_id

    def test_lineage_validation_detects_cost_model_type_change(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        cached_key = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=SlippageModel(SlippageModelConfig()),
        )
        current_key = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(),
        )
        result = validate_simulation_lineage(cached_key, current_key)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.COST_MODEL_TYPE_CHANGED

    def test_lineage_validation_detects_slippage_config_change(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        cached_key = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(
                VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("25"))
            ),
        )
        current_key = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(
                VolumeShareSlippageConfig(impact_coefficient_bps=Decimal("100"))
            ),
        )
        result = validate_simulation_lineage(cached_key, current_key)
        assert not result.compatible
        assert result.reason == CacheInvalidationReason.SLIPPAGE_CHANGED

    def test_identical_volume_share_configs_produce_same_key(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(VolumeShareSlippageConfig()),
        )
        k2 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            slippage_model=VolumeShareSlippageModel(VolumeShareSlippageConfig()),
        )
        assert k1.key_id == k2.key_id
