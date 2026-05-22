"""
Lineage-safe cache validation.

Before a cached simulation result may be reused, ``validate_simulation_lineage``
verifies that every field affecting computation semantics is identical between
the cached key and the current request key.  This prevents silent reuse of
stale or incompatible results when dataset versions, model parameters, or
execution semantics have changed.

The validation is field-by-field so that the ``CacheInvalidationReason`` pinpoints
exactly what changed — making misses debuggable.
"""

from __future__ import annotations

from dataclasses import dataclass

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
)


@dataclass(frozen=True)
class LineageValidationResult:
    """Outcome of a lineage compatibility check."""

    compatible: bool
    reason: CacheInvalidationReason | None

    @classmethod
    def ok(cls) -> LineageValidationResult:
        return cls(compatible=True, reason=None)

    @classmethod
    def incompatible(cls, reason: CacheInvalidationReason) -> LineageValidationResult:
        return cls(compatible=False, reason=reason)

    def explain(self) -> str:
        if self.compatible:
            return "LINEAGE OK: cached result is compatible with the current request"
        return f"LINEAGE INCOMPATIBLE: {self.reason.value if self.reason else 'unknown'}"


def validate_simulation_lineage(
    cached: SimulationCacheKey,
    current: SimulationCacheKey,
) -> LineageValidationResult:
    """Check whether a cached simulation key is semantically compatible with
    the current simulation request.

    Returns ``LineageValidationResult.ok()`` only when every field matches
    exactly.  The first mismatch terminates the check and names the cause.

    This function is the authoritative guard against stale cache reuse — it
    must be called even when the ``key_id`` values match, as a defence-in-depth
    check.
    """
    checks: list[tuple[str, str, CacheInvalidationReason]] = [
        (cached.config_hash, current.config_hash, CacheInvalidationReason.CONFIG_CHANGED),
        (
            cached.dataset_version,
            current.dataset_version,
            CacheInvalidationReason.DATASET_VERSION_CHANGED,
        ),
        (
            cached.universe_version,
            current.universe_version,
            CacheInvalidationReason.UNIVERSE_VERSION_CHANGED,
        ),
        (cached.price_basis, current.price_basis, CacheInvalidationReason.PRICE_BASIS_CHANGED),
        (cached.symbols_hash, current.symbols_hash, CacheInvalidationReason.SYMBOLS_CHANGED),
        (cached.start_date, current.start_date, CacheInvalidationReason.DATE_RANGE_CHANGED),
        (cached.end_date, current.end_date, CacheInvalidationReason.DATE_RANGE_CHANGED),
        (str(cached.random_seed), str(current.random_seed), CacheInvalidationReason.SEED_CHANGED),
        (cached.stage_name, current.stage_name, CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED),
        (cached.window_role, current.window_role, CacheInvalidationReason.WINDOW_SEMANTICS_CHANGED),
        (cached.fill_policy, current.fill_policy, CacheInvalidationReason.FILL_POLICY_CHANGED),
        (cached.slippage_rate, current.slippage_rate, CacheInvalidationReason.SLIPPAGE_CHANGED),
        (
            cached.commission_per_share,
            current.commission_per_share,
            CacheInvalidationReason.COMMISSION_CHANGED,
        ),
        (
            cached.regime_dataset_version,
            current.regime_dataset_version,
            CacheInvalidationReason.REGIME_VERSION_CHANGED,
        ),
        (
            cached.feature_versions_hash,
            current.feature_versions_hash,
            CacheInvalidationReason.FEATURE_VERSION_CHANGED,
        ),
    ]

    for cached_val, current_val, reason in checks:
        if cached_val != current_val:
            return LineageValidationResult.incompatible(reason)

    return LineageValidationResult.ok()
