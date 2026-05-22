"""Research caching infrastructure (TASK-3.4).

Public surface:
    StrategyGenerationCacheKey, SimulationCacheKey, CacheInvalidationReason
    CacheLookupResult, CacheHitMetadata
    StrategyGenerationCache
    SimulationResultCache
    build_generation_cache_key, build_simulation_cache_key
    validate_simulation_lineage, LineageValidationResult
"""

from autonomous_trading_platform.research.cache.cache_identity import (
    CacheInvalidationReason,
    SimulationCacheKey,
    StrategyGenerationCacheKey,
)
from autonomous_trading_platform.research.cache.cache_key_builder import (
    build_generation_cache_key,
    build_simulation_cache_key,
)
from autonomous_trading_platform.research.cache.cache_lookup_result import (
    CacheHitMetadata,
    CacheLookupResult,
)
from autonomous_trading_platform.research.cache.cache_validation import (
    LineageValidationResult,
    validate_simulation_lineage,
)
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.cache.strategy_generation_cache import (
    StrategyGenerationCache,
)

__all__ = [
    "CacheHitMetadata",
    "CacheInvalidationReason",
    "CacheLookupResult",
    "LineageValidationResult",
    "SimulationCacheKey",
    "SimulationResultCache",
    "StrategyGenerationCache",
    "StrategyGenerationCacheKey",
    "build_generation_cache_key",
    "build_simulation_cache_key",
    "validate_simulation_lineage",
]
