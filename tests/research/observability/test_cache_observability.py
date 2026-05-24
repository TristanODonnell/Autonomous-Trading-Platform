"""Tests for research cache hit/miss OTel counter export."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def _sim_key(seed: int = 42) -> SimulationCacheKey:
    return SimulationCacheKey(
        config_hash="abc123",
        dataset_version="v1",
        universe_version="u1",
        price_basis=PriceBasis.RAW.value,
        symbols_hash="sym",
        start_date=str(date(2024, 1, 1)),
        end_date=str(date(2024, 6, 1)),
        random_seed=seed,
        stage_name="cheap",
        window_role="full",
        fill_policy="next_open",
        latency_bars=1,
        cost_model_type="volume_share",
        slippage_config_hash="abc1234567890123",
        commission_per_share="0.0",
        regime_dataset_version="",
        feature_versions_hash="",
    )


def _strategy_config(strategy_id: str = "s1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


class TestSimulationCacheObservability:
    def test_miss_increments_otel_miss_counter(self):
        cache = SimulationResultCache()
        with patch(
            "autonomous_trading_platform.research.cache.simulation_result_cache.research_cache_lookups"
        ) as mock_counter:
            cache.lookup(_sim_key())
        mock_counter.add.assert_called_once_with(1, {"cache_type": "simulation", "result": "miss"})

    def test_hit_increments_otel_hit_counter(self):
        cache = SimulationResultCache()
        key = _sim_key()
        cache.record(key, run_id="run-1")
        with patch(
            "autonomous_trading_platform.research.cache.simulation_result_cache.research_cache_lookups"
        ) as mock_counter:
            cache.lookup(key)
        mock_counter.add.assert_called_once_with(1, {"cache_type": "simulation", "result": "hit"})

    def test_multiple_lookups_increment_independently(self):
        cache = SimulationResultCache()
        key = _sim_key()
        cache.record(key, run_id="run-1")
        with patch(
            "autonomous_trading_platform.research.cache.simulation_result_cache.research_cache_lookups"
        ) as mock_counter:
            cache.lookup(key)  # hit
            cache.lookup(_sim_key(seed=99))  # miss (different seed)
        assert mock_counter.add.call_count == 2
        results = [c.args[1]["result"] for c in mock_counter.add.call_args_list]
        assert "hit" in results
        assert "miss" in results

    def test_lineage_mismatch_emits_miss(self):
        cache = SimulationResultCache()
        key = _sim_key()
        cache.record(key, run_id="run-1")
        # Different key with same seed but different dataset_version
        key_v2 = SimulationCacheKey(
            config_hash="abc123",
            dataset_version="v2",  # different
            universe_version="u1",
            price_basis=PriceBasis.RAW.value,
            symbols_hash="sym",
            start_date=str(date(2024, 1, 1)),
            end_date=str(date(2024, 6, 1)),
            random_seed=42,
            stage_name="cheap",
            window_role="full",
            fill_policy="next_open",
            latency_bars=1,
            cost_model_type="volume_share",
            slippage_config_hash="abc1234567890123",
            commission_per_share="0.0",
            regime_dataset_version="",
            feature_versions_hash="",
        )
        with patch(
            "autonomous_trading_platform.research.cache.simulation_result_cache.research_cache_lookups"
        ) as mock_counter:
            cache.lookup(key_v2)
        mock_counter.add.assert_called_once_with(1, {"cache_type": "simulation", "result": "miss"})

    def test_internal_counters_still_updated(self):
        cache = SimulationResultCache()
        key = _sim_key()
        cache.record(key, run_id="run-1")
        cache.lookup(key)  # hit
        cache.lookup(_sim_key(seed=99))  # miss
        stats = cache.stats()
        assert stats["session_hits"] == 1
        assert stats["session_misses"] == 1


class TestGenerationCacheObservability:
    def test_miss_increments_otel_miss_counter(self):
        cache = StrategyGenerationCache()
        with patch(
            "autonomous_trading_platform.research.cache.strategy_generation_cache.research_cache_lookups"
        ) as mock_counter:
            cache.check(_strategy_config())
        mock_counter.add.assert_called_once_with(1, {"cache_type": "generation", "result": "miss"})

    def test_hit_increments_otel_hit_counter(self):
        cache = StrategyGenerationCache()
        config = _strategy_config()
        cache.record_generated(config, run_id="gen-1")
        with patch(
            "autonomous_trading_platform.research.cache.strategy_generation_cache.research_cache_lookups"
        ) as mock_counter:
            cache.check(config)
        mock_counter.add.assert_called_once_with(1, {"cache_type": "generation", "result": "hit"})

    def test_cache_type_label_is_generation(self):
        cache = StrategyGenerationCache()
        with patch(
            "autonomous_trading_platform.research.cache.strategy_generation_cache.research_cache_lookups"
        ) as mock_counter:
            cache.check(_strategy_config("new-strat"))
        labels = mock_counter.add.call_args.args[1]
        assert labels["cache_type"] == "generation"

    def test_multiple_different_configs_each_miss(self):
        cache = StrategyGenerationCache()
        with patch(
            "autonomous_trading_platform.research.cache.strategy_generation_cache.research_cache_lookups"
        ) as mock_counter:
            cache.check(_strategy_config("s1"))
            cache.check(_strategy_config("s2"))
        assert mock_counter.add.call_count == 2
        for c in mock_counter.add.call_args_list:
            assert c.args[1]["result"] == "miss"
