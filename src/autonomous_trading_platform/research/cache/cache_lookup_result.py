"""
Cache lookup result types with full provenance metadata.

Every cache query returns a ``CacheLookupResult``.  On a hit the ``metadata``
field carries provenance: which run produced the cached result, when it was
cached, and the source artifact path.  On a miss the ``invalidation_reason``
explains why no compatible entry was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autonomous_trading_platform.research.cache.cache_identity import CacheInvalidationReason


@dataclass(frozen=True)
class CacheHitMetadata:
    """Provenance for a successful cache lookup."""

    cache_key: str
    cached_run_id: str
    cached_at: datetime
    config_hash: str
    source_artifact: str | None = None
    strategy_type: str | None = None
    hit_count: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "cache_key": self.cache_key,
            "cached_run_id": self.cached_run_id,
            "cached_at": self.cached_at.isoformat(),
            "config_hash": self.config_hash,
            "source_artifact": self.source_artifact,
            "strategy_type": self.strategy_type,
            "hit_count": self.hit_count,
        }


@dataclass(frozen=True)
class CacheLookupResult:
    """Result of a single cache lookup — hit or miss with full provenance."""

    hit: bool
    metadata: CacheHitMetadata | None = None
    invalidation_reason: CacheInvalidationReason | None = None

    @classmethod
    def miss(
        cls,
        reason: CacheInvalidationReason = CacheInvalidationReason.NOT_FOUND,
    ) -> CacheLookupResult:
        return cls(hit=False, metadata=None, invalidation_reason=reason)

    @classmethod
    def found(cls, metadata: CacheHitMetadata) -> CacheLookupResult:
        return cls(hit=True, metadata=metadata, invalidation_reason=None)

    def explain(self) -> str:
        """Human-readable explanation of the lookup outcome."""
        if self.hit and self.metadata:
            return (
                f"CACHE HIT  key={self.metadata.cache_key[:16]}…"
                f"  run_id={self.metadata.cached_run_id}"
                f"  cached_at={self.metadata.cached_at.isoformat()}"
            )
        reason = self.invalidation_reason or CacheInvalidationReason.NOT_FOUND
        return f"CACHE MISS  reason={reason.value}"

    def to_dict(self) -> dict[str, object]:
        return {
            "hit": self.hit,
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "invalidation_reason": self.invalidation_reason.value
            if self.invalidation_reason
            else None,
        }
