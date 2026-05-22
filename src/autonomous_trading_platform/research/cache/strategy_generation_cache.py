"""
Strategy generation cache (TASK-3.4A).

Provides deterministic, cross-session deduplication of generated strategy
configurations.  Extends the existing in-session ``seen: set[str]`` deduplication
in ``StrategyGenerationEngine`` with:

- Persistent storage of generation history across sessions (optional)
- Provenance metadata per deduplicated config
- Inspectable hit/miss statistics
- Integration with ``GenerationSummary`` via ``record_generated`` / ``check``

The cache is keyed on ``StrategyConfig.config_hash()`` — two configs with
identical normalised parameters are identical by definition.

Thread safety: all mutations are protected by a ``threading.Lock``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autonomous_trading_platform.observability.metrics import research_cache_lookups
from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    StrategyGenerationCacheKey,
)
from autonomous_trading_platform.research.cache.cache_key_builder import build_generation_cache_key
from autonomous_trading_platform.research.cache.cache_lookup_result import (
    CacheHitMetadata,
    CacheLookupResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)

_CACHE_TYPE = "generation"


class StrategyGenerationCache:
    """Persistent deduplication cache for generated strategy configurations.

    Parameters
    ----------
    persist_path:
        Optional path to a JSON file for cross-session persistence.  When
        ``None`` the cache is in-memory only (reset on process exit).
    """

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._persist_path: Path | None = Path(persist_path) if persist_path else None
        if self._persist_path and self._persist_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, config: StrategyConfig) -> CacheLookupResult:
        """Check whether a config has already been generated.

        Returns a hit if an identical config_hash has been recorded before,
        miss otherwise.  Does NOT record the config — call ``record_generated``
        after the config is accepted.
        """
        key = build_generation_cache_key(config)
        with self._lock:
            entry = self._entries.get(key.key_id)
            if entry is None:
                self._misses += 1
                research_cache_lookups.add(1, {"cache_type": _CACHE_TYPE, "result": "miss"})
                return CacheLookupResult.miss(CacheInvalidationReason.NOT_FOUND)
            self._hits += 1
            research_cache_lookups.add(1, {"cache_type": _CACHE_TYPE, "result": "hit"})
            metadata = CacheHitMetadata(
                cache_key=key.key_id,
                cached_run_id=entry["first_seen_run_id"],
                cached_at=datetime.fromisoformat(entry["first_seen_at"]),
                config_hash=key.config_hash,
                strategy_type=key.strategy_type,
                hit_count=entry["hit_count"],
            )
            return CacheLookupResult.found(metadata)

    def record_generated(
        self,
        config: StrategyConfig,
        *,
        run_id: str = "generation",
        source_artifact: str | None = None,
    ) -> StrategyGenerationCacheKey:
        """Record that a config has been accepted (not deduplicated).

        Safe to call multiple times — idempotent on key_id.  Returns the key
        used for storage.
        """
        key = build_generation_cache_key(config)
        now = datetime.now(UTC).isoformat()
        with self._lock:
            if key.key_id not in self._entries:
                self._entries[key.key_id] = {
                    "config_hash": key.config_hash,
                    "strategy_type": key.strategy_type,
                    "first_seen_at": now,
                    "first_seen_run_id": run_id,
                    "source_artifact": source_artifact,
                    "hit_count": 0,
                    "generation_count": 1,
                }
            else:
                self._entries[key.key_id]["generation_count"] = (
                    self._entries[key.key_id].get("generation_count", 1) + 1
                )
            self._maybe_persist()
        return key

    def record_duplicate(self, config: StrategyConfig) -> None:
        """Increment the hit_count for an already-known config."""
        key = build_generation_cache_key(config)
        with self._lock:
            if key.key_id in self._entries:
                self._entries[key.key_id]["hit_count"] = (
                    self._entries[key.key_id].get("hit_count", 0) + 1
                )
            self._maybe_persist()

    def known_hashes(self) -> frozenset[str]:
        """Return the set of all known config_hash values (for fast set lookups)."""
        with self._lock:
            return frozenset(self._entries)

    def stats(self) -> dict[str, Any]:
        """Cache statistics summary."""
        with self._lock:
            total_hits = sum(e.get("hit_count", 0) for e in self._entries.values())
            return {
                "total_entries": len(self._entries),
                "session_hits": self._hits,
                "session_misses": self._misses,
                "total_duplicates_prevented": total_hits,
                "persist_path": str(self._persist_path) if self._persist_path else None,
            }

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._maybe_persist()

    def entries(self) -> list[dict[str, Any]]:
        """Snapshot of all cache entries for inspection."""
        with self._lock:
            return list(self._entries.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _maybe_persist(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._entries, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("strategy generation cache: could not persist to %s", self._persist_path)

    def _load(self) -> None:
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            if isinstance(raw, dict):
                self._entries = raw
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "strategy generation cache: could not load %s — starting empty",
                self._persist_path,
            )
