"""
Simulation result cache (TASK-3.4B).

Avoids re-running simulations when strategy config, dataset lineage, and
execution semantics are identical to a prior run.

The cache is keyed on ``SimulationCacheKey.key_id`` — a SHA-256 of all fields
that semantically determine simulation output (config hash, dataset version,
universe version, fill model, slippage model, commission model, symbols,
date range, seed, stage/window semantics, regime and feature dataset versions).

Lookup is exact-match only — no fuzzy or approximate reuse.

On every lookup the lineage fields of the stored key are validated against the
current request key via ``validate_simulation_lineage``.  This defence-in-depth
check ensures a key_id collision (however improbable) cannot cause stale reuse.

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
    SimulationCacheKey,
)
from autonomous_trading_platform.research.cache.cache_lookup_result import (
    CacheHitMetadata,
    CacheLookupResult,
)
from autonomous_trading_platform.research.cache.cache_validation import validate_simulation_lineage

logger = logging.getLogger(__name__)

_CACHE_TYPE = "simulation"


class SimulationResultCache:
    """Persistent exact-match cache for simulation results.

    Parameters
    ----------
    persist_path:
        Optional path to a JSON file for cross-session persistence.  When
        ``None`` the cache is in-memory only.
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

    def lookup(self, key: SimulationCacheKey) -> CacheLookupResult:
        """Look up whether a simulation with ``key`` has been run before.

        Returns a hit only when:
        1. The ``key_id`` is present in the cache, AND
        2. The stored key passes ``validate_simulation_lineage`` against ``key``.

        The lineage check is defence-in-depth — semantically different keys
        must never reuse each other's results.
        """
        key_id = key.key_id
        with self._lock:
            entry = self._entries.get(key_id)
            if entry is None:
                self._misses += 1
                research_cache_lookups.add(1, {"cache_type": _CACHE_TYPE, "result": "miss"})
                return CacheLookupResult.miss(CacheInvalidationReason.NOT_FOUND)

            stored_key = _entry_to_key(entry["key"])
            lineage = validate_simulation_lineage(stored_key, key)
            if not lineage.compatible:
                self._misses += 1
                research_cache_lookups.add(1, {"cache_type": _CACHE_TYPE, "result": "miss"})
                return CacheLookupResult.miss(lineage.reason or CacheInvalidationReason.NOT_FOUND)

            self._hits += 1
            research_cache_lookups.add(1, {"cache_type": _CACHE_TYPE, "result": "hit"})
            entry["hit_count"] = entry.get("hit_count", 0) + 1
            metadata = CacheHitMetadata(
                cache_key=key_id,
                cached_run_id=entry["run_id"],
                cached_at=datetime.fromisoformat(entry["cached_at"]),
                config_hash=entry["key"]["config_hash"],
                source_artifact=entry.get("source_artifact"),
                strategy_type=entry.get("strategy_type"),
                hit_count=entry["hit_count"],
            )
            self._maybe_persist()
            return CacheLookupResult.found(metadata)

    def record(
        self,
        key: SimulationCacheKey,
        *,
        run_id: str,
        source_artifact: str | None = None,
        strategy_type: str | None = None,
    ) -> None:
        """Record a completed simulation so future identical runs can be skipped.

        Idempotent: recording the same ``key_id`` twice does not overwrite an
        existing entry (the first completed run is canonical).
        """
        key_id = key.key_id
        now = datetime.now(UTC).isoformat()
        with self._lock:
            if key_id not in self._entries:
                self._entries[key_id] = {
                    "key": key.to_dict(),
                    "key_id": key_id,
                    "run_id": run_id,
                    "cached_at": now,
                    "source_artifact": source_artifact,
                    "strategy_type": strategy_type,
                    "hit_count": 0,
                }
                self._maybe_persist()

    def invalidate(self, key: SimulationCacheKey) -> bool:
        """Remove a single entry from the cache.  Returns True if removed."""
        key_id = key.key_id
        with self._lock:
            existed = key_id in self._entries
            self._entries.pop(key_id, None)
            if existed:
                self._maybe_persist()
            return existed

    def stats(self) -> dict[str, Any]:
        """Summary statistics for the cache."""
        with self._lock:
            total_hits = sum(e.get("hit_count", 0) for e in self._entries.values())
            return {
                "total_entries": len(self._entries),
                "session_hits": self._hits,
                "session_misses": self._misses,
                "total_simulations_prevented": total_hits,
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

    def explain(self, key: SimulationCacheKey) -> str:
        """Return a human-readable string explaining a lookup result."""
        result = self.lookup(key)
        return result.explain()

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
            logger.warning("simulation result cache: could not persist to %s", self._persist_path)

    def _load(self) -> None:
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            if isinstance(raw, dict):
                self._entries = raw
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "simulation result cache: could not load %s — starting empty",
                self._persist_path,
            )


def _entry_to_key(key_dict: dict[str, Any]) -> SimulationCacheKey:
    return SimulationCacheKey(
        config_hash=key_dict["config_hash"],
        dataset_version=key_dict["dataset_version"],
        universe_version=key_dict["universe_version"],
        price_basis=key_dict["price_basis"],
        symbols_hash=key_dict["symbols_hash"],
        start_date=key_dict["start_date"],
        end_date=key_dict["end_date"],
        random_seed=int(key_dict["random_seed"]),
        stage_name=key_dict["stage_name"],
        window_role=key_dict["window_role"],
        fill_policy=key_dict["fill_policy"],
        latency_bars=int(key_dict.get("latency_bars", 0)),
        slippage_rate=key_dict["slippage_rate"],
        commission_per_share=key_dict["commission_per_share"],
        regime_dataset_version=key_dict["regime_dataset_version"],
        feature_versions_hash=key_dict["feature_versions_hash"],
    )
