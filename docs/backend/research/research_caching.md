# Research Caching Architecture

## Purpose

The research caching layer prevents redundant computation across three
categories of work:

| Category | Problem prevented |
|---|---|
| Strategy generation | Re-generating identical `StrategyConfig` objects |
| Simulation execution | Re-running simulations with identical inputs |
| Downstream analysis | (future) skipping regime analysis / validation when simulation is cached |

All caching is **exact-match only**.  No fuzzy or approximate reuse is
performed.  Every cache hit requires that all semantic inputs are byte-for-byte
identical.

---

## Location

```
src/autonomous_trading_platform/research/cache/
    __init__.py
    cache_identity.py          ← key dataclasses + CacheInvalidationReason
    cache_lookup_result.py     ← CacheLookupResult, CacheHitMetadata
    cache_key_builder.py       ← factory functions: build_generation_cache_key,
                                                      build_simulation_cache_key
    cache_validation.py        ← validate_simulation_lineage, LineageValidationResult
    strategy_generation_cache.py  ← StrategyGenerationCache (TASK-3.4A)
    simulation_result_cache.py    ← SimulationResultCache (TASK-3.4B)
```

---

## Cache Key Design

### StrategyGenerationCacheKey

```python
@dataclass(frozen=True)
class StrategyGenerationCacheKey:
    config_hash: str    # SHA-256 of canonical StrategyConfig JSON
    strategy_type: str

    @property
    def key_id(self) -> str:
        return self.config_hash
```

The `key_id` is the `config_hash` produced by `StrategyConfig.config_hash()`.
Two configs with identical normalised parameters always produce the same hash,
regardless of when or how they were generated.

### SimulationCacheKey

The simulation key captures every field that affects simulation output:

| Field | Semantics |
|---|---|
| `config_hash` | SHA-256 of strategy config canonical JSON |
| `dataset_version` | Bar dataset version |
| `universe_version` | Universe snapshot version |
| `price_basis` | `adjusted` or `unadjusted` |
| `symbols_hash` | SHA-256[:16] of sorted symbol list |
| `start_date` | ISO date string |
| `end_date` | ISO date string |
| `random_seed` | Deterministic seed |
| `stage_name` | Pipeline stage (`train`, `test`, etc.) |
| `window_role` | Walk-forward window role |
| `fill_policy` | `current_close` or `next_open` |
| `slippage_rate` | Decimal string |
| `commission_per_share` | Decimal string |
| `regime_dataset_version` | Regime feature dataset version (empty string if unused) |
| `feature_versions_hash` | SHA-256[:16] of sorted {feature: version} dict |

The `key_id` is the SHA-256 of the canonical JSON of all fields (keys sorted,
no float precision issues — Decimals stored as strings).

---

## Lookup Protocol

### Generation cache lookup

```python
result = cache.check(config)          # → CacheLookupResult
if result.hit:
    # skip generation
    summary.duplicate(config_hash=result.metadata.config_hash, ...)
else:
    # generate
    cache.record_generated(config, run_id=run_id)
```

### Simulation cache lookup

```python
key = build_simulation_cache_key(
    strategy_config=config,
    run_config=run_config,
    fill_model=fill_model,
    slippage_model=slippage_model,
    cost_model=cost_model,
    regime_dataset_version=regime_version,
    feature_versions=feature_versions,
)
result = sim_cache.lookup(key)        # → CacheLookupResult
if result.hit:
    # reuse cached run_id
    return result.metadata.cached_run_id
else:
    run_id = execute_simulation(...)
    sim_cache.record(key, run_id=run_id)
```

---

## Lineage-Safe Validation

Before any cached simulation result may be reused, `validate_simulation_lineage`
checks every field individually.  The first mismatch returns a
`LineageValidationResult` with the specific `CacheInvalidationReason`.

```python
result = validate_simulation_lineage(cached_key, current_key)
if not result.compatible:
    print(result.explain())
    # → "LINEAGE INCOMPATIBLE: dataset_version_changed"
```

This runs **inside** `SimulationResultCache.lookup()` as defence-in-depth even
when the `key_id` values match.

### Invalidation reasons

| Reason | Trigger |
|---|---|
| `not_found` | No entry in cache |
| `config_changed` | Strategy parameters changed |
| `dataset_version_changed` | Bar data re-ingested with new version |
| `universe_version_changed` | Universe snapshot rotated |
| `price_basis_changed` | Switched between adjusted / unadjusted |
| `symbols_changed` | Symbol list changed |
| `date_range_changed` | Backtest window shifted |
| `seed_changed` | Random seed changed |
| `window_semantics_changed` | Stage name or window role changed |
| `fill_policy_changed` | Fill model changed |
| `slippage_changed` | Slippage rate changed |
| `commission_changed` | Commission model changed |
| `regime_version_changed` | Regime feature dataset re-computed |
| `feature_version_changed` | Any feature dataset re-computed |

---

## Cache Provenance

Every hit returns `CacheHitMetadata`:

```python
@dataclass(frozen=True)
class CacheHitMetadata:
    cache_key: str           # key_id (SHA-256)
    cached_run_id: str       # original run that produced the result
    cached_at: datetime      # UTC timestamp of the first recording
    config_hash: str         # strategy config hash
    source_artifact: str     # optional path to original artifact
    strategy_type: str       # strategy type string
    hit_count: int           # number of times this entry was reused
```

All misses return `CacheLookupResult.miss(reason)` with the specific reason.
Callers can call `result.explain()` for a human-readable string.

---

## Persistence

Both caches support optional JSON persistence:

```python
# In-memory only (reset on process exit)
gen_cache = StrategyGenerationCache()

# Cross-session persistent
gen_cache = StrategyGenerationCache(
    persist_path="data/research/cache/generation/cache.json"
)
sim_cache = SimulationResultCache(
    persist_path="data/research/cache/simulations/cache.json"
)
```

The JSON files are human-readable.  Each entry is a dict keyed on `key_id`.

---

## CLI Tooling

```bash
# Statistics summary
python scripts/inspect_cache.py stats
python scripts/inspect_cache.py stats --output json

# List all entries
python scripts/inspect_cache.py entries --cache simulation

# Explain a specific key
python scripts/inspect_cache.py explain --cache simulation --key-id <sha256>

# Clear a cache (requires --confirm)
python scripts/inspect_cache.py clear --cache generation --confirm
```

---

## Thread Safety

Both cache classes use `threading.Lock` around all mutations.  They are safe
for concurrent use within a single process.  Cross-process coordination is out
of scope (see Deferred Work below).

Parallel simulation stages perform cache lookups and records per unit. Duplicate
records for the same cache key remain idempotent, so concurrent units cannot
replace the canonical first completed entry. Serial and parallel execution use
the same request fields for cache key construction, including deterministic
stage/window roles and per-unit seeds.

---

## Invariants

1. **No approximate matches.** A cache hit requires every field to be identical.
2. **Lineage validated on every hit.** `validate_simulation_lineage` runs
   inside `SimulationResultCache.lookup()` — stale entries cannot be reused
   silently.
3. **First recording is canonical.** `record()` and `record_generated()` are
   idempotent — a second call with the same key_id is a no-op, preserving the
   original `run_id`.
4. **Decimals serialised as strings.** Slippage and commission rates are stored
   as `str(Decimal(...))` to avoid floating-point representation drift between
   Python versions.
5. **Symbols are sorted before hashing.** `["AAPL", "SPY"]` and
   `["SPY", "AAPL"]` produce the same `symbols_hash`.

---

## Deferred Future Work

- **Distributed cache** (Redis / shared filesystem) — not yet implemented.
  Current implementation is single-process.
- **Approximate cache matching** — explicitly out of scope.  Only exact-match
  reuse is semantically safe.
- **Dependency-graph artifact reuse** — if a simulation is cached, downstream
  validation / regime analysis artifacts may also be reusable.  Full
  dependency-graph orchestration is a future task.
- **Online / live-runtime caching** — out of scope.  Cache is for research
  pipeline only.
- **TTL-based expiry** — not implemented.  Cache entries are permanent until
  manually cleared.
