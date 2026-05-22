"""
Factory functions for constructing cache keys from research configuration objects.

All hashing is deterministic and canonical: sorted keys, no floats in JSON
(Decimals converted to strings), symbols sorted before hashing.
"""

from __future__ import annotations

import hashlib
import json

from autonomous_trading_platform.research.cache.cache_identity import (
    SimulationCacheKey,
    StrategyGenerationCacheKey,
)
from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

_DEFAULT_FILL = SimulatedFillModelConfig()
_DEFAULT_SLIPPAGE = SlippageModelConfig()
_DEFAULT_COST = SimulationCostModelConfig()


def build_generation_cache_key(config: StrategyConfig) -> StrategyGenerationCacheKey:
    """Build the generation cache key for a StrategyConfig."""
    return StrategyGenerationCacheKey(
        config_hash=config.config_hash(),
        strategy_type=config.type,
    )


def build_simulation_cache_key(
    *,
    strategy_config: StrategyConfig,
    run_config: SimulationRunConfig,
    fill_model: SimulatedFillModelConfig | None = None,
    slippage_model: SlippageModelConfig | None = None,
    cost_model: SimulationCostModelConfig | None = None,
    universe_version: str = "v1",
    regime_dataset_version: str | None = None,
    feature_versions: dict[str, str] | None = None,
) -> SimulationCacheKey:
    """Build a fully-specified simulation cache key.

    Parameters
    ----------
    strategy_config:
        The strategy being simulated.
    run_config:
        Validated simulation run configuration.
    fill_model:
        Fill policy; defaults to ``SimulatedFillModelConfig()`` when omitted.
    slippage_model:
        Slippage model; defaults to ``SlippageModelConfig()`` when omitted.
    cost_model:
        Cost model; defaults to ``SimulationCostModelConfig()`` when omitted.
    universe_version:
        Universe snapshot version; defaults to ``"v1"``.
    regime_dataset_version:
        Regime feature dataset version, or ``None`` when not used.
    feature_versions:
        Dict mapping feature name → dataset version for any feature datasets
        consumed by this simulation.
    """
    fill = fill_model if fill_model is not None else _DEFAULT_FILL
    slippage = slippage_model if slippage_model is not None else _DEFAULT_SLIPPAGE
    cost = cost_model if cost_model is not None else _DEFAULT_COST

    return SimulationCacheKey(
        config_hash=strategy_config.config_hash(),
        dataset_version=run_config.dataset_version,
        universe_version=universe_version,
        price_basis=run_config.price_basis.value,
        symbols_hash=_hash_symbols(run_config.symbols),
        start_date=str(run_config.start_date),
        end_date=str(run_config.end_date),
        random_seed=run_config.random_seed,
        stage_name=run_config.stage_name or "default",
        window_role=run_config.window_role or "default",
        fill_policy=fill.market_fill_policy.value,
        latency_bars=fill.latency_bars,
        slippage_rate=str(slippage.slippage_rate),
        commission_per_share=str(cost.commission_per_share),
        regime_dataset_version=regime_dataset_version or "",
        feature_versions_hash=_hash_feature_versions(feature_versions),
    )


def _hash_symbols(symbols: list[str]) -> str:
    """Deterministic 16-char hash of a symbol list (order-independent)."""
    payload = json.dumps(sorted(symbols), separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _hash_feature_versions(versions: dict[str, str] | None) -> str:
    """Deterministic 16-char hash of a feature-version mapping."""
    if not versions:
        return ""
    payload = json.dumps(
        {k: versions[k] for k in sorted(versions)},
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
