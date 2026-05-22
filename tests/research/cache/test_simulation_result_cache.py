"""Tests for SimulationResultCache (TASK-3.4B)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
)
from autonomous_trading_platform.research.cache.cache_key_builder import (
    build_simulation_cache_key,
)
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig
from autonomous_trading_platform.research.simulation.models.fill_model import (
    MarketFillPolicy,
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def _make_run_config(**overrides: Any) -> SimulationRunConfig:
    base: dict[str, Any] = dict(
        strategy_id="test-s",
        strategy_type="moving_average_crossover",
        parameters={"short_window": 10, "long_window": 30},
        dataset_version="v1",
        random_seed=42,
        price_basis="adjusted",
        symbols=["SPY"],
        start_date="2022-01-01",
        end_date="2023-01-01",
    )
    base.update(overrides)
    return SimulationRunConfig.model_validate(base)  # type: ignore[no-any-return]


def _make_strategy_config(**overrides) -> StrategyConfig:
    base = dict(
        strategy_id="test-s",
        type="moving_average_crossover",
        parameters={"short_window": 10, "long_window": 30},
    )
    base.update(overrides)
    return StrategyConfig(**base)


def _make_sim_key(**overrides: Any) -> SimulationCacheKey:
    defaults: dict[str, Any] = dict(
        config_hash="cfghash",
        dataset_version="v1",
        universe_version="v1",
        price_basis="adjusted",
        symbols_hash="symhash",
        start_date="2022-01-01",
        end_date="2023-01-01",
        random_seed=42,
        stage_name="default",
        window_role="default",
        fill_policy="current_close",
        slippage_rate="0.0001",
        commission_per_share="0.0000",
        regime_dataset_version="",
        feature_versions_hash="",
    )
    defaults.update(overrides)
    return SimulationCacheKey(**defaults)


class TestSimulationResultCacheInMemory:
    def setup_method(self):
        self.cache = SimulationResultCache()

    def test_miss_on_empty_cache(self):
        key = _make_sim_key()
        result = self.cache.lookup(key)
        assert not result.hit
        assert result.invalidation_reason == CacheInvalidationReason.NOT_FOUND

    def test_hit_after_record(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        result = self.cache.lookup(key)
        assert result.hit
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-1"

    def test_record_is_idempotent(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        self.cache.record(key, run_id="run-2")
        result = self.cache.lookup(key)
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-1"

    def test_different_key_ids_independent(self):
        k1 = _make_sim_key(dataset_version="v1")
        k2 = _make_sim_key(dataset_version="v2")
        self.cache.record(k1, run_id="run-1")
        assert not self.cache.lookup(k2).hit

    def test_invalidate_removes_entry(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        removed = self.cache.invalidate(key)
        assert removed
        assert not self.cache.lookup(key).hit

    def test_invalidate_returns_false_for_unknown(self):
        key = _make_sim_key()
        assert not self.cache.invalidate(key)

    def test_stats_after_lookups(self):
        key = _make_sim_key()
        self.cache.lookup(key)  # miss
        self.cache.record(key, run_id="run-1")
        self.cache.lookup(key)  # hit
        stats = self.cache.stats()
        assert stats["session_hits"] == 1
        assert stats["session_misses"] == 1
        assert stats["total_entries"] == 1

    def test_hit_increments_hit_count(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        self.cache.lookup(key)
        self.cache.lookup(key)
        entries = self.cache.entries()
        assert entries[0]["hit_count"] == 2

    def test_clear_empties_cache(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        self.cache.clear()
        assert not self.cache.lookup(key).hit
        assert self.cache.stats()["total_entries"] == 0

    def test_metadata_includes_source_artifact(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1", source_artifact="simulations/metrics/...")
        result = self.cache.lookup(key)
        assert result.metadata is not None
        assert result.metadata.source_artifact == "simulations/metrics/..."

    def test_metadata_includes_strategy_type(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1", strategy_type="momentum")
        result = self.cache.lookup(key)
        assert result.metadata is not None
        assert result.metadata.strategy_type == "momentum"

    def test_explain_returns_string(self):
        key = _make_sim_key()
        explanation = self.cache.explain(key)
        assert isinstance(explanation, str)
        assert "CACHE MISS" in explanation

    def test_explain_hit_message(self):
        key = _make_sim_key()
        self.cache.record(key, run_id="run-1")
        explanation = self.cache.explain(key)
        assert "CACHE HIT" in explanation


class TestSimulationResultCachePersistence:
    def test_entries_survive_reload(self, tmp_path: Path):
        cache_file = tmp_path / "sim_cache.json"
        key = _make_sim_key()

        cache1 = SimulationResultCache(persist_path=cache_file)
        cache1.record(key, run_id="run-persisted")

        cache2 = SimulationResultCache(persist_path=cache_file)
        result = cache2.lookup(key)
        assert result.hit
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-persisted"

    def test_corrupt_file_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "sim_cache.json"
        cache_file.write_text("{broken json", encoding="utf-8")
        cache = SimulationResultCache(persist_path=cache_file)
        assert cache.stats()["total_entries"] == 0


class TestSimulationCacheKeyBuilder:
    def test_key_is_deterministic(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run)
        assert k1.key_id == k2.key_id

    def test_symbol_order_independent(self):
        cfg = _make_strategy_config()
        r1 = _make_run_config(symbols=["SPY", "AAPL"])
        r2 = _make_run_config(symbols=["AAPL", "SPY"])
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=r1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=r2)
        assert k1.symbols_hash == k2.symbols_hash
        assert k1.key_id == k2.key_id

    def test_different_symbols_produce_different_hash(self):
        cfg = _make_strategy_config()
        r1 = _make_run_config(symbols=["SPY"])
        r2 = _make_run_config(symbols=["AAPL"])
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=r1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=r2)
        assert k1.key_id != k2.key_id

    def test_slippage_model_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        s1 = SlippageModelConfig(slippage_rate=Decimal("0.0001"))
        s2 = SlippageModelConfig(slippage_rate=Decimal("0.0005"))
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run, slippage_model=s1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run, slippage_model=s2)
        assert k1.key_id != k2.key_id

    def test_fill_model_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        f1 = SimulatedFillModelConfig(market_fill_policy=MarketFillPolicy.CURRENT_CLOSE)
        f2 = SimulatedFillModelConfig(market_fill_policy=MarketFillPolicy.NEXT_OPEN)
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run, fill_model=f1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run, fill_model=f2)
        assert k1.key_id != k2.key_id

    def test_commission_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        c1 = SimulationCostModelConfig(commission_per_share=Decimal("0.00"))
        c2 = SimulationCostModelConfig(commission_per_share=Decimal("0.005"))
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run, cost_model=c1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run, cost_model=c2)
        assert k1.key_id != k2.key_id

    def test_regime_version_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(
            strategy_config=cfg, run_config=run, regime_dataset_version=None
        )
        k2 = build_simulation_cache_key(
            strategy_config=cfg, run_config=run, regime_dataset_version="reg_v2"
        )
        assert k1.key_id != k2.key_id

    def test_feature_versions_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run, feature_versions=None)
        k2 = build_simulation_cache_key(
            strategy_config=cfg, run_config=run, feature_versions={"rsi": "v2"}
        )
        assert k1.key_id != k2.key_id

    def test_feature_versions_order_independent(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            feature_versions={"rsi": "v1", "macd": "v2"},
        )
        k2 = build_simulation_cache_key(
            strategy_config=cfg,
            run_config=run,
            feature_versions={"macd": "v2", "rsi": "v1"},
        )
        assert k1.key_id == k2.key_id

    def test_universe_version_included(self):
        cfg = _make_strategy_config()
        run = _make_run_config()
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run, universe_version="v1")
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run, universe_version="v2")
        assert k1.key_id != k2.key_id

    def test_stage_name_defaults_when_none(self):
        cfg = _make_strategy_config()
        run1 = _make_run_config(stage_name=None)
        run2 = _make_run_config(stage_name=None)
        k1 = build_simulation_cache_key(strategy_config=cfg, run_config=run1)
        k2 = build_simulation_cache_key(strategy_config=cfg, run_config=run2)
        assert k1.key_id == k2.key_id
        assert k1.stage_name == "default"
