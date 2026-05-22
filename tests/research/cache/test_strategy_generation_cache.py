"""Tests for StrategyGenerationCache (TASK-3.4A)."""

from __future__ import annotations

from pathlib import Path

from autonomous_trading_platform.research.cache.cache_identity import CacheInvalidationReason
from autonomous_trading_platform.research.cache.cache_key_builder import build_generation_cache_key
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def _make_config(strategy_type: str = "momentum", **params) -> StrategyConfig:
    default_params: dict = {}
    if strategy_type == "momentum":
        default_params = {"lookback": 20, "buy_above": 0.01, "sell_below": -0.01}
    elif strategy_type == "mean_reversion":
        default_params = {"window": 20, "buy_below_z": -2.0, "sell_above_z": 2.0}
    default_params.update(params)
    return StrategyConfig(
        strategy_id="test-strategy",
        type=strategy_type,
        parameters=default_params,
    )


class TestStrategyGenerationCacheInMemory:
    def setup_method(self):
        self.cache = StrategyGenerationCache()

    def test_miss_on_empty_cache(self):
        config = _make_config()
        result = self.cache.check(config)
        assert not result.hit
        assert result.invalidation_reason == CacheInvalidationReason.NOT_FOUND

    def test_hit_after_record(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        result = self.cache.check(config)
        assert result.hit
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-1"

    def test_hit_metadata_contains_config_hash(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        result = self.cache.check(config)
        assert result.metadata is not None
        assert result.metadata.config_hash == config.config_hash()

    def test_hit_metadata_contains_strategy_type(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        result = self.cache.check(config)
        assert result.metadata is not None
        assert result.metadata.strategy_type == "momentum"

    def test_different_configs_do_not_collide(self):
        c1 = _make_config(lookback=10)
        c2 = _make_config(lookback=20)
        assert c1.config_hash() != c2.config_hash()
        self.cache.record_generated(c1, run_id="run-1")
        assert not self.cache.check(c2).hit

    def test_record_is_idempotent(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        self.cache.record_generated(config, run_id="run-2")
        # First run_id is canonical
        result = self.cache.check(config)
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-1"

    def test_record_duplicate_increments_hit_count(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        self.cache.record_duplicate(config)
        self.cache.record_duplicate(config)
        entries = self.cache.entries()
        entry = next(e for e in entries if e["config_hash"] == config.config_hash())
        assert entry["hit_count"] == 2

    def test_stats_reflect_session_activity(self):
        config = _make_config()
        self.cache.check(config)  # miss
        self.cache.record_generated(config, run_id="run-1")
        self.cache.check(config)  # hit
        stats = self.cache.stats()
        assert stats["session_hits"] == 1
        assert stats["session_misses"] == 1
        assert stats["total_entries"] == 1

    def test_clear_removes_all_entries(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        self.cache.clear()
        assert not self.cache.check(config).hit
        assert self.cache.stats()["total_entries"] == 0

    def test_known_hashes_returns_frozenset(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        known = self.cache.known_hashes()
        assert isinstance(known, frozenset)
        assert config.config_hash() in known

    def test_multiple_strategy_types_independent(self):
        c1 = _make_config("momentum")
        c2 = _make_config("mean_reversion")
        self.cache.record_generated(c1, run_id="run-1")
        assert not self.cache.check(c2).hit

    def test_explain_miss(self):
        config = _make_config()
        result = self.cache.check(config)
        assert "CACHE MISS" in result.explain()

    def test_explain_hit(self):
        config = _make_config()
        self.cache.record_generated(config, run_id="run-1")
        result = self.cache.check(config)
        assert "CACHE HIT" in result.explain()


class TestStrategyGenerationCachePersistence:
    def test_entries_survive_reload(self, tmp_path: Path):
        cache_file = tmp_path / "gen_cache.json"
        config = _make_config()

        cache1 = StrategyGenerationCache(persist_path=cache_file)
        cache1.record_generated(config, run_id="run-persisted")

        cache2 = StrategyGenerationCache(persist_path=cache_file)
        result = cache2.check(config)
        assert result.hit
        assert result.metadata is not None
        assert result.metadata.cached_run_id == "run-persisted"

    def test_clear_persists_empty_state(self, tmp_path: Path):
        cache_file = tmp_path / "gen_cache.json"
        config = _make_config()

        cache1 = StrategyGenerationCache(persist_path=cache_file)
        cache1.record_generated(config, run_id="run-1")
        cache1.clear()

        cache2 = StrategyGenerationCache(persist_path=cache_file)
        assert not cache2.check(config).hit

    def test_corrupt_file_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "gen_cache.json"
        cache_file.write_text("not valid json", encoding="utf-8")

        cache = StrategyGenerationCache(persist_path=cache_file)
        assert cache.stats()["total_entries"] == 0

    def test_nonexistent_persist_path_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "nonexistent" / "cache.json"
        cache = StrategyGenerationCache(persist_path=cache_file)
        assert cache.stats()["total_entries"] == 0


class TestGenerationCacheKeyBuilder:
    def test_key_is_deterministic(self):
        config = _make_config()
        k1 = build_generation_cache_key(config)
        k2 = build_generation_cache_key(config)
        assert k1.key_id == k2.key_id

    def test_key_id_equals_config_hash(self):
        config = _make_config()
        key = build_generation_cache_key(config)
        assert key.key_id == config.config_hash()

    def test_strategy_type_captured(self):
        config = _make_config("momentum")
        key = build_generation_cache_key(config)
        assert key.strategy_type == "momentum"
