from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from itertools import combinations
from typing import Any

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationSummary,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

# ---------------------------------------------------------------------------
# Domain metadata — governs which indicators can drive which rule types.
# Kept here rather than in ComponentDefinition: this is generator-specific
# knowledge, not a contract the rest of the system needs to know about.
# ---------------------------------------------------------------------------

_INDICATOR_DOMAIN: dict[str, str] = {
    # zero_centered: naturally crosses 0; threshold with ±10 variants is meaningful
    "momentum": "zero_centered",
    "rate_of_change": "zero_centered",
    "z_score": "zero_centered",
    "distance_from_moving_average": "zero_centered",
    # ratio: hovers around 1.0; threshold at 1.0/1.2 is the buy/sell split
    "volume_ratio": "ratio",
    # price_series: raw price levels; crossover or comparison only, not threshold
    "simple_moving_average": "price_series",
    "exponential_moving_average": "price_series",
    # positive: always > 0; useful as filter indicator only
    "rolling_standard_deviation": "positive",
    "realized_volatility": "positive",
    "average_volume": "positive",
    # excluded from threshold: RSI 0-100 range exceeds the ±10 schema cap
    "rsi": "0_100",
    "rsi_wilder": "0_100",
    # excluded from all rules: boolean output is not numeric
    "volume_spike": "boolean",
}

# Threshold parameter variants that stay within the schema's ±10 bounds
_THRESHOLD_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "zero_centered": [
        {"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.58},  # trend-following
        {"buy_below": -1.5, "sell_above": 1.5, "confidence": 0.62},  # mild mean-reversion
        {"buy_below": -2.0, "sell_above": 2.0, "confidence": 0.65},  # strong mean-reversion
    ],
    "ratio": [
        {"buy_above": 1.0, "sell_below": 1.0, "confidence": 0.55},  # above/below average
        {"buy_above": 1.2, "confidence": 0.58},  # strong volume only
    ],
}

# ---------------------------------------------------------------------------
# Named indicator instances used when building skeletons.
# Each entry: (instance_id, component_name, params_dict)
# ---------------------------------------------------------------------------

_ZERO_CENTERED: list[tuple[str, str, dict[str, Any]]] = [
    ("mom_5", "momentum", {"lookback": 5}),
    ("mom_10", "momentum", {"lookback": 10}),
    ("mom_20", "momentum", {"lookback": 20}),
    ("roc_5", "rate_of_change", {"lookback": 5}),
    ("roc_10", "rate_of_change", {"lookback": 10}),
    ("roc_20", "rate_of_change", {"lookback": 20}),
    ("z_10", "z_score", {"window": 10}),
    ("z_20", "z_score", {"window": 20}),
    ("dist_20", "distance_from_moving_average", {"window": 20}),
    ("dist_50", "distance_from_moving_average", {"window": 50}),
]

_RATIO: list[tuple[str, str, dict[str, Any]]] = [
    ("vol_r_10", "volume_ratio", {"window": 10}),
    ("vol_r_20", "volume_ratio", {"window": 20}),
    ("vol_r_30", "volume_ratio", {"window": 30}),
]

# Instances used exclusively as filter/gating indicators (never as entry drivers)
_REGIME_FILTER_INDS: list[tuple[str, str, dict[str, Any]]] = [
    ("sma_50", "simple_moving_average", {"window": 50}),
    ("ema_50", "exponential_moving_average", {"window": 50}),
]

_VOL_FILTER_INDS: list[tuple[str, str, dict[str, Any]]] = [
    ("roll_std_20", "rolling_standard_deviation", {"window": 20}),
]

# Same-family windows for crossover (pairs are always fast → slow, ascending window)
_CROSSOVER_FAMILIES: list[list[tuple[str, str, dict[str, Any]]]] = [
    [
        ("sma_5", "simple_moving_average", {"window": 5}),
        ("sma_10", "simple_moving_average", {"window": 10}),
        ("sma_20", "simple_moving_average", {"window": 20}),
        ("sma_50", "simple_moving_average", {"window": 50}),
        ("sma_100", "simple_moving_average", {"window": 100}),
    ],
    [
        ("ema_5", "exponential_moving_average", {"window": 5}),
        ("ema_10", "exponential_moving_average", {"window": 10}),
        ("ema_20", "exponential_moving_average", {"window": 20}),
        ("ema_50", "exponential_moving_average", {"window": 50}),
        ("ema_100", "exponential_moving_average", {"window": 100}),
    ],
    [
        ("mom_5", "momentum", {"lookback": 5}),
        ("mom_10", "momentum", {"lookback": 10}),
        ("mom_20", "momentum", {"lookback": 20}),
    ],
    [
        ("roc_5", "rate_of_change", {"lookback": 5}),
        ("roc_10", "rate_of_change", {"lookback": 10}),
        ("roc_20", "rate_of_change", {"lookback": 20}),
    ],
]

# SMA vs EMA at the same window — cross-type comparison pairs
_PRICE_COMPARISON_PAIRS: list[
    tuple[tuple[str, str, dict[str, Any]], tuple[str, str, dict[str, Any]]]
] = [
    (
        ("sma_10", "simple_moving_average", {"window": 10}),
        ("ema_10", "exponential_moving_average", {"window": 10}),
    ),
    (
        ("sma_20", "simple_moving_average", {"window": 20}),
        ("ema_20", "exponential_moving_average", {"window": 20}),
    ),
    (
        ("sma_50", "simple_moving_average", {"window": 50}),
        ("ema_50", "exponential_moving_average", {"window": 50}),
    ),
]

_SINGLE_RULE_AGGS: list[dict[str, Any]] = [
    {"component": "voting", "parameters": {"min_votes": 1}},
    {"component": "weighted_score", "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55}},
]

_MULTI_RULE_AGGS: list[dict[str, Any]] = [
    {"component": "logical_and", "parameters": {}},
    {"component": "logical_or", "parameters": {}},
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_composite_rule_configs(
    *,
    options: GenerationOptions | None = None,
    method: str = "grid",
) -> tuple[list[StrategyConfig], GenerationSummary]:
    summary = GenerationSummary()
    configs: list[StrategyConfig] = []
    seen: set[str] = set()

    for template in _selected_templates(method=method, options=options or GenerationOptions()):
        summary.generated_count += 1
        try:
            _validate_template_components(template)
            config = make_config("composite_rule", template)
        except ValueError as exc:
            summary.reject(
                str(exc),
                strategy_type="composite_rule",
                parameters=template,
                generator=method,
            )
            continue
        config_hash = config.config_hash()
        if config_hash in seen:
            summary.duplicate(
                strategy_type="composite_rule",
                config_hash=config_hash,
                generator=method,
                parameters=template,
            )
            continue
        seen.add(config_hash)
        summary.accepted_count += 1
        summary.strategy_type_distribution["composite_rule"] += 1
        summary.family_distribution["composite"] += 1
        configs.append(config)

    return configs, summary


def _selected_templates(
    *,
    method: str,
    options: GenerationOptions,
) -> Iterator[dict[str, Any]]:
    templates = _templates()
    if method == "random":
        import random

        rng = random.Random(options.seed)
        shuffled = list(templates)
        rng.shuffle(shuffled)
        yield from shuffled[: max(1, min(options.n_samples, len(shuffled)))]
        return
    if method == "evolutionary":
        yield from templates
        for index, template in enumerate(templates[: max(0, options.generations)]):
            mutated = deepcopy(template)
            mutated["metadata"]["generation_template"] += f"_mutation_{index}"
            if mutated["aggregator"]["component"] == "voting":
                mutated["aggregator"] = {
                    "component": "weighted_score",
                    "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55},
                }
            else:
                mutated["aggregator"] = {"component": "voting", "parameters": {"min_votes": 1}}
            yield mutated
        return
    yield from templates


def _templates() -> list[dict[str, Any]]:
    return _build_skeletons()


# ---------------------------------------------------------------------------
# Programmatic skeleton generator
# ---------------------------------------------------------------------------


def _build_skeletons() -> list[dict[str, Any]]:
    """
    Enumerate structural skeletons by combining indicators, rule types, aggregators,
    and optional filters from module-level domain tables. No registry calls needed —
    compatibility is encoded in _INDICATOR_DOMAIN and the instance lists above.

    Pattern counts (before validation / dedup):
      threshold  : ~288  (zero_centered × 3 variants × 2 agg × 4 filter options
                          + ratio × 2 variants × 2 agg × 4 filter options)
      crossover  : ~104  (same-family pairs × 2 agg × with/without vol confirmation)
      comparison : ~102  (zero_centered pairs + ratio pairs + price cross-type pairs)
      two-rule   : ~360  (zero_centered × 3 variants × ratio × 2 vol-variants × 2 logical agg)
      ─────────────────
      total pool : ~854 skeletons → n_samples=30 draws from a genuinely diverse pool
    """
    out: list[dict[str, Any]] = []
    out.extend(_threshold_skeletons())
    out.extend(_crossover_skeletons())
    out.extend(_comparison_skeletons())
    out.extend(_two_rule_skeletons())
    return out


def _threshold_skeletons() -> list[dict[str, Any]]:
    """Single indicator → threshold rule, with optional regime or volatility gate."""
    out: list[dict[str, Any]] = []

    # Filter options: (filter_type_tag, filter_instance_or_None)
    filter_opts: list[tuple[str, tuple[str, str, dict[str, Any]] | None]] = [
        ("none", None),
        *[("reg", fi) for fi in _REGIME_FILTER_INDS],
        *[("vol", fi) for fi in _VOL_FILTER_INDS],
    ]

    for instances, domain in [(_ZERO_CENTERED, "zero_centered"), (_RATIO, "ratio")]:
        for vi, variant in enumerate(_THRESHOLD_VARIANTS[domain]):
            for ind_id, ind_comp, ind_params in instances:
                for agg in _SINGLE_RULE_AGGS:
                    agg_tag = "voti" if agg["component"] == "voting" else "weig"
                    for ftag, finstance in filter_opts:
                        indicators = [_ind(ind_id, ind_comp, **ind_params)]
                        filters: list[dict[str, Any]] = []

                        if finstance is not None:
                            fid, fcomp, fparams = finstance
                            if fid != ind_id:
                                indicators.append(_ind(fid, fcomp, **fparams))
                                if ftag == "reg":
                                    filters = [_regime_filter(fid)]
                                else:
                                    filters = [_volatility_filter(fid)]

                        name_parts = [f"thresh_{ind_id}_v{vi}_{agg_tag}"]
                        if filters:
                            name_parts.append(f"f{ftag}_{finstance[0]}")  # type: ignore[index]
                        out.append(
                            _t(
                                "_".join(name_parts),
                                indicators=indicators,
                                entry_rules=[_threshold_rule(ind_id, **variant)],
                                aggregator=agg,
                                filters=filters,
                            )
                        )
    return out


def _crossover_skeletons() -> list[dict[str, Any]]:
    """Same-family indicator pairs wired to the crossover rule."""
    out: list[dict[str, Any]] = []
    for family in _CROSSOVER_FAMILIES:
        for (fast_id, fast_comp, fast_p), (slow_id, slow_comp, slow_p) in combinations(family, 2):
            for agg in _SINGLE_RULE_AGGS:
                agg_tag = "voti" if agg["component"] == "voting" else "weig"
                base_inds = [
                    _ind(fast_id, fast_comp, **fast_p),
                    _ind(slow_id, slow_comp, **slow_p),
                ]
                # Without volume confirmation
                out.append(
                    _t(
                        f"xover_{fast_id}_x_{slow_id}_{agg_tag}",
                        indicators=base_inds,
                        entry_rules=[_crossover_rule(fast_id, slow_id)],
                        aggregator=agg,
                        confidence_mode="weighted",
                    )
                )
                # With volume confirmation
                out.append(
                    _t(
                        f"xover_{fast_id}_x_{slow_id}_{agg_tag}_volc",
                        indicators=[*base_inds, _ind("vol_r_20", "volume_ratio", window=20)],
                        entry_rules=[_crossover_rule(fast_id, slow_id)],
                        confirmations=[_volume_confirm("vol_r_20")],
                        aggregator=agg,
                        confidence_mode="weighted",
                    )
                )
    return out


def _comparison_skeletons() -> list[dict[str, Any]]:
    """Two indicators compared via the comparison rule."""
    out: list[dict[str, Any]] = []

    # Zero-centered × zero-centered — all C(10, 2) pairs
    for (a_id, a_comp, a_p), (b_id, b_comp, b_p) in combinations(_ZERO_CENTERED, 2):
        for agg in _SINGLE_RULE_AGGS:
            agg_tag = "voti" if agg["component"] == "voting" else "weig"
            out.append(
                _t(
                    f"cmp_{a_id}_vs_{b_id}_{agg_tag}",
                    indicators=[_ind(a_id, a_comp, **a_p), _ind(b_id, b_comp, **b_p)],
                    entry_rules=[_comparison_rule(a_id, b_id)],
                    aggregator=agg,
                )
            )

    # Ratio × ratio — all C(3, 2) pairs
    for (a_id, a_comp, a_p), (b_id, b_comp, b_p) in combinations(_RATIO, 2):
        for agg in _SINGLE_RULE_AGGS:
            agg_tag = "voti" if agg["component"] == "voting" else "weig"
            out.append(
                _t(
                    f"cmp_{a_id}_vs_{b_id}_{agg_tag}",
                    indicators=[_ind(a_id, a_comp, **a_p), _ind(b_id, b_comp, **b_p)],
                    entry_rules=[_comparison_rule(a_id, b_id)],
                    aggregator=agg,
                )
            )

    # Price series: SMA vs EMA at same window (cross-type, not covered by crossover)
    for (a_id, a_comp, a_p), (b_id, b_comp, b_p) in _PRICE_COMPARISON_PAIRS:
        for agg in _SINGLE_RULE_AGGS:
            agg_tag = "voti" if agg["component"] == "voting" else "weig"
            out.append(
                _t(
                    f"cmp_{a_id}_vs_{b_id}_{agg_tag}",
                    indicators=[_ind(a_id, a_comp, **a_p), _ind(b_id, b_comp, **b_p)],
                    entry_rules=[_comparison_rule(a_id, b_id)],
                    aggregator=agg,
                )
            )

    return out


def _two_rule_skeletons() -> list[dict[str, Any]]:
    """
    Zero-centered threshold + volume-ratio threshold → logical AND/OR aggregator.
    Structural intent: primary momentum/mean-reversion signal that ALSO requires
    volume confirmation (AND) or fires on either signal alone (OR).
    """
    out: list[dict[str, Any]] = []
    for pri_id, pri_comp, pri_p in _ZERO_CENTERED:
        for vi, variant in enumerate(_THRESHOLD_VARIANTS["zero_centered"]):
            for vol_id, vol_comp, vol_p in _RATIO:
                for vol_variant in _THRESHOLD_VARIANTS["ratio"]:
                    for agg in _MULTI_RULE_AGGS:
                        agg_tag = "and" if agg["component"] == "logical_and" else "or"
                        vol_vtag = "hi" if vol_variant.get("buy_above", 0) > 1.0 else "lo"
                        out.append(
                            _t(
                                f"logic_{agg_tag}_{pri_id}_v{vi}_{vol_id}_{vol_vtag}",
                                indicators=[
                                    _ind(pri_id, pri_comp, **pri_p),
                                    _ind(vol_id, vol_comp, **vol_p),
                                ],
                                entry_rules=[
                                    _threshold_rule(pri_id, rule_id="primary_rule", **variant),
                                    _threshold_rule(
                                        vol_id,
                                        rule_id="vol_rule",
                                        weight=0.8,
                                        **vol_variant,
                                    ),
                                ],
                                aggregator=agg,
                            )
                        )
    return out


# ---------------------------------------------------------------------------
# Template construction helpers
# ---------------------------------------------------------------------------


def _ind(ind_id: str, component: str, **params: Any) -> dict[str, Any]:
    return {"id": ind_id, "component": component, "parameters": params}


def _threshold_rule(
    ind_id: str,
    *,
    rule_id: str = "entry",
    weight: float = 1.0,
    **threshold_kwargs: Any,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "component": "threshold",
        "inputs": {"value": {"indicator_id": ind_id, "offset": 0}},
        "parameters": threshold_kwargs,
        "weight": weight,
    }


def _crossover_rule(
    fast_id: str,
    slow_id: str,
    *,
    rule_id: str = "crossover_entry",
    confidence: float = 0.65,
    weight: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "component": "crossover",
        "inputs": {
            "previous_fast": {"indicator_id": fast_id, "offset": -1},
            "previous_slow": {"indicator_id": slow_id, "offset": -1},
            "current_fast": {"indicator_id": fast_id, "offset": 0},
            "current_slow": {"indicator_id": slow_id, "offset": 0},
        },
        "parameters": {"confidence": confidence},
        "weight": weight,
    }


def _comparison_rule(
    left_id: str,
    right_id: str,
    *,
    rule_id: str = "comparison_entry",
    confidence: float = 0.60,
    weight: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "component": "comparison",
        "inputs": {
            "left_value": {"indicator_id": left_id, "offset": 0},
            "right_value": {"indicator_id": right_id, "offset": 0},
        },
        "parameters": {"confidence": confidence},
        "weight": weight,
    }


def _volume_confirm(vol_id: str = "vol_r_20", *, weight: float = 0.5) -> dict[str, Any]:
    return _threshold_rule(
        vol_id,
        rule_id="vol_confirm",
        weight=weight,
        buy_above=1.0,
        sell_below=1.0,
        confidence=0.55,
    )


def _regime_filter(ind_id: str, operator: str = "gt", threshold: float = 0.0) -> dict[str, Any]:
    return {
        "id": "regime_filter",
        "component": "regime_filter",
        "input": {"indicator_id": ind_id, "offset": 0},
        "operator": operator,
        "threshold": threshold,
        "block_reason": "regime_filter_blocked",
    }


def _volatility_filter(ind_id: str, operator: str = "lt", threshold: float = 5.0) -> dict[str, Any]:
    return {
        "id": "vol_filter",
        "component": "volatility_filter",
        "input": {"indicator_id": ind_id, "offset": 0},
        "operator": operator,
        "threshold": threshold,
        "block_reason": "volatility_filter_blocked",
    }


def _t(
    name: str,
    indicators: list[dict[str, Any]],
    entry_rules: list[dict[str, Any]],
    aggregator: dict[str, Any],
    *,
    filters: list[dict[str, Any]] | None = None,
    confirmations: list[dict[str, Any]] | None = None,
    confidence_mode: str = "aggregation",
) -> dict[str, Any]:
    return {
        "strategy_type": "composite_rule",
        "indicators": indicators,
        "entry_rules": entry_rules,
        "filters": filters or [],
        "confirmations": confirmations or [],
        "aggregator": aggregator,
        "confidence_scoring": {"mode": confidence_mode, "floor": 0.0, "cap": 1.0},
        "sizing": {},
        "metadata": {"generation_template": name},
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_template_components(template: dict[str, Any]) -> None:
    registry = get_component_registry()
    for indicator in template["indicators"]:
        defn = registry.get_component_definition(indicator["component"])
        if defn.component_type != ComponentType.INDICATOR or not defn.is_executable:
            raise ValueError(f"{indicator['component']} is not an executable indicator")
    for rule in [*template["entry_rules"], *template["confirmations"]]:
        defn = registry.get_component_definition(rule["component"])
        if defn.component_type != ComponentType.SIGNAL_RULE or not defn.is_executable:
            raise ValueError(f"{rule['component']} is not an executable signal rule")
    aggregator = registry.get_component_definition(template["aggregator"]["component"])
    if aggregator.component_type != ComponentType.AGGREGATOR or not aggregator.is_executable:
        raise ValueError(f"{aggregator.component_name} is not an executable aggregator")
    for filter_cfg in template["filters"]:
        defn = registry.get_component_definition(filter_cfg["component"])
        if defn.component_type != ComponentType.FILTER:
            raise ValueError(f"{filter_cfg['component']} is not a filter component")
