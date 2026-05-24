"""
Factory functions for constructing cache keys from research configuration objects.

All hashing is deterministic and canonical: sorted keys, no floats in JSON
(Decimals converted to strings), symbols sorted before hashing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent
from autonomous_trading_platform.research.cache.cache_identity import (
    SimulationCacheKey,
    StrategyGenerationCacheKey,
)
from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageModel,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

_DEFAULT_FILL = SimulatedFillModelConfig()
_DEFAULT_SLIPPAGE_MODEL: Any = VolumeShareSlippageModel()
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
    slippage_model: Any | None = None,
    cost_model: SimulationCostModelConfig | None = None,
    universe_version: str = "v1",
    regime_dataset_version: str | None = None,
    feature_versions: dict[str, str] | None = None,
    calibration_snapshot_id: str = "",
    adverse_threshold_bps: str = "10",
    settlement_days: int | None = None,
    dividend_events: list[DividendEvent] | None = None,
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
        Slippage model instance (SlippageModel, VolumeShareSlippageModel,
        SpreadAwareSlippageModel, or any object with model_type and config_summary()).
        Defaults to ``VolumeShareSlippageModel()`` when omitted.
    cost_model:
        Cost model config; defaults to ``SimulationCostModelConfig()`` when omitted.
    universe_version:
        Universe snapshot version; defaults to ``"v1"``.
    regime_dataset_version:
        Regime feature dataset version, or ``None`` when not used.
    feature_versions:
        Dict mapping feature name → dataset version for any feature datasets
        consumed by this simulation.
    calibration_snapshot_id:
        Calibration snapshot UUID used to derive slippage model parameters, or ``""``
        when no calibration is applied.  Changing this value changes the cache key so
        simulations with different calibrations never collide.
    adverse_threshold_bps:
        Adverse fill threshold (in bps, as string) that governs fill quality
        annotations for this simulation run.  Simulations with different thresholds
        produce distinct cache entries.  Defaults to ``"10"`` (platform default).
    settlement_days:
        Settlement delay in simulation trading bars.  When ``None``, the value is
        read from ``run_config.settlement_days`` (recommended).  Pass explicitly to
        override the run config value.
    dividend_events:
        Cash dividend events applied during simulation.  When ``None``, events are
        read from ``run_config.dividend_events``.  Changing this list produces a
        distinct cache entry to preserve lineage reproducibility.
    """
    fill = fill_model if fill_model is not None else _DEFAULT_FILL
    slippage = slippage_model if slippage_model is not None else _DEFAULT_SLIPPAGE_MODEL
    cost = cost_model if cost_model is not None else _DEFAULT_COST
    effective_settlement_days = (
        settlement_days if settlement_days is not None else run_config.settlement_days
    )
    effective_dividend_events = (
        dividend_events if dividend_events is not None else run_config.dividend_events
    )

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
        cost_model_type=slippage.model_type.value,
        slippage_config_hash=_hash_slippage_config(slippage),
        commission_per_share=str(cost.commission_per_share),
        regime_dataset_version=regime_dataset_version or "",
        feature_versions_hash=_hash_feature_versions(feature_versions),
        calibration_snapshot_id=calibration_snapshot_id,
        adverse_threshold_bps=adverse_threshold_bps,
        settlement_days=effective_settlement_days,
        dividend_events_hash=_hash_dividend_events(effective_dividend_events),
    )


def _hash_slippage_config(slippage_model: Any) -> str:
    """Deterministic 16-char hash of a slippage model's config summary."""
    summary = slippage_model.config_summary()
    payload = json.dumps(
        {k: summary[k] for k in sorted(summary)},
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


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


def _hash_dividend_events(events: list[DividendEvent]) -> str:
    """Deterministic 16-char hash of a dividend event list (order-independent)."""
    if not events:
        return ""
    items = sorted(
        [
            {
                "cash_amount_per_share": str(e.cash_amount_per_share),
                "ex_date": str(e.ex_date),
                "symbol": e.symbol,
            }
            for e in events
        ],
        key=lambda x: (x["symbol"], x["ex_date"]),
    )
    payload = json.dumps(items, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
