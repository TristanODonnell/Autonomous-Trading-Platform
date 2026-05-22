"""
Canonical cache key types for research caching.

Every cache operation is keyed on one of these frozen dataclasses.  The
``key_id`` property is the authoritative stable string used for lookups and
storage.  All fields that affect computation semantics are included so that
semantically different runs can never collide.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class CacheInvalidationReason(StrEnum):
    NOT_FOUND = "not_found"
    CONFIG_CHANGED = "config_changed"
    DATASET_VERSION_CHANGED = "dataset_version_changed"
    FEATURE_VERSION_CHANGED = "feature_version_changed"
    REGIME_VERSION_CHANGED = "regime_version_changed"
    UNIVERSE_VERSION_CHANGED = "universe_version_changed"
    FILL_POLICY_CHANGED = "fill_policy_changed"
    SLIPPAGE_CHANGED = "slippage_changed"
    COMMISSION_CHANGED = "commission_changed"
    SEED_CHANGED = "seed_changed"
    SYMBOLS_CHANGED = "symbols_changed"
    DATE_RANGE_CHANGED = "date_range_changed"
    PRICE_BASIS_CHANGED = "price_basis_changed"
    WINDOW_SEMANTICS_CHANGED = "window_semantics_changed"


@dataclass(frozen=True)
class StrategyGenerationCacheKey:
    """Identity key for a generated strategy configuration.

    The ``key_id`` is the strategy ``config_hash`` — two StrategyConfig objects
    with the same normalised parameters produce the same key, regardless of when
    or how they were generated.
    """

    config_hash: str
    strategy_type: str

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise ValueError("config_hash must not be empty")
        if not self.strategy_type:
            raise ValueError("strategy_type must not be empty")

    @property
    def key_id(self) -> str:
        return self.config_hash

    def to_dict(self) -> dict[str, str]:
        return {
            "config_hash": self.config_hash,
            "strategy_type": self.strategy_type,
        }


@dataclass(frozen=True)
class SimulationCacheKey:
    """Identity key for a simulation result.

    Captures every semantic input that affects simulation output.  Two runs are
    cache-equivalent only when all fields match exactly.  The ``key_id`` is a
    SHA-256 of the canonical JSON representation.
    """

    config_hash: str
    dataset_version: str
    universe_version: str
    price_basis: str
    symbols_hash: str
    start_date: str
    end_date: str
    random_seed: int
    stage_name: str
    window_role: str
    fill_policy: str
    slippage_rate: str
    commission_per_share: str
    regime_dataset_version: str
    feature_versions_hash: str

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise ValueError("config_hash must not be empty")
        if not self.dataset_version:
            raise ValueError("dataset_version must not be empty")

    @property
    def key_id(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "config_hash": self.config_hash,
            "commission_per_share": self.commission_per_share,
            "dataset_version": self.dataset_version,
            "end_date": self.end_date,
            "feature_versions_hash": self.feature_versions_hash,
            "fill_policy": self.fill_policy,
            "price_basis": self.price_basis,
            "random_seed": self.random_seed,
            "regime_dataset_version": self.regime_dataset_version,
            "slippage_rate": self.slippage_rate,
            "stage_name": self.stage_name,
            "start_date": self.start_date,
            "symbols_hash": self.symbols_hash,
            "universe_version": self.universe_version,
            "window_role": self.window_role,
        }
