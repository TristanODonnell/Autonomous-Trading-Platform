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
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    MIN_RESEARCH_WINDOW_BARS,
)
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

# rate_of_change is a fraction (0.01 = 1%), so the +-1.5 / +-2.0 above cannot fire on it:
# no 5-minute bar of 31 names in March-April 2026 moved 150% in 10 or 20 bars (research
# plan G5: the 80 such skeletons acted as 7 behaviours). Its two mean-reversion variants
# are sized as z-score's are, at 1.5 and 2 standard deviations, but of rate_of_change
# itself, measured per lookback on those bars (~88k values each, rounded):
#   lookback 10: std 0.0102 -> 0.015 / 0.020 (|roc| reaches them on 7.6% / 4.4% of bars)
#   lookback 20: std 0.0144 -> 0.022 / 0.029 (8.9% / 4.9%)
# Fixed here, not computed per run, so a template (and its id) does not move with the
# data. A fixed fraction is still a higher bar for a quiet name than a busy one (the
# per-name std ran 0.005 to 0.019 at lookback 10); z_score is the per-name scaled form.
_ROC_MEAN_REVERSION_BANDS: dict[int, tuple[float, float]] = {
    10: (0.015, 0.020),
    20: (0.022, 0.029),
}


# At threshold zero some rules are one rule by arithmetic, on any bars (plan G5, found by
# the re-measure): momentum and rate of change of one lookback have the same sign (a
# price is positive, so p - p0 > 0 exactly when p / p0 - 1 > 0), and so do distance
# from the average and z-score of one window (z is that distance over a positive std).
# The at-zero rule is kept for the indicator in its own scale (rate of change, z-score)
# and not built for its dollar twin; the twin's +-1.5 / +-2.0 variants are other rules
# and stay. The research simulator sizes without the signal's confidence, so equal
# direction on every bar is equal trading.
_SAME_SIGN_TWIN: dict[str, tuple[str, str]] = {
    "momentum": ("rate_of_change", "lookback"),
    "distance_from_moving_average": ("z_score", "window"),
}


def _zero_rule_has_a_twin(component: str, params: dict[str, Any]) -> bool:
    """True when the pool holds the twin that makes this indicator's at-zero rule."""
    if component not in _SAME_SIGN_TWIN:
        return False
    twin_component, key = _SAME_SIGN_TWIN[component]
    return any(
        comp == twin_component and twin_params.get(key) == params.get(key)
        for _, comp, twin_params in _ZERO_CENTERED
    )


def _is_same_sign_comparison(
    a: tuple[str, str, dict[str, Any]], b: tuple[str, str, dict[str, Any]]
) -> bool:
    """Momentum against rate of change of one lookback compares dollars with a fraction:
    (p - p0) > (p / p0 - 1) exactly when p > p0, for any p0 above one dollar, so it is
    the at-zero rule again. Every name the platform trades is above a dollar."""
    return {a[1], b[1]} == {"momentum", "rate_of_change"} and a[2].get("lookback") == b[2].get(
        "lookback"
    )


def _threshold_variants_for(component: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """The zero-centered variants in the indicator's own units: v0 (at zero) is the same
    for all; for rate_of_change v1 / v2 take the measured bands above."""
    variants = _THRESHOLD_VARIANTS["zero_centered"]
    if component != "rate_of_change":
        return variants
    lookback = int(params["lookback"])
    if lookback not in _ROC_MEAN_REVERSION_BANDS:
        raise ValueError(
            f"rate_of_change lookback {lookback} has no measured band: "
            "add it to _ROC_MEAN_REVERSION_BANDS"
        )
    mild, strong = _ROC_MEAN_REVERSION_BANDS[lookback]
    return [
        variants[0],
        {**variants[1], "buy_below": -mild, "sell_above": mild},
        {**variants[2], "buy_below": -strong, "sell_above": strong},
    ]


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

# Regime gate: trade only while price is above its 50-bar average. A filter compares one
# indicator with a constant, so the gate reads price minus the average (dist_50 > 0).
# It used to read the average itself (sma_50 > 0, ema_50 > 0), which a price always
# passes: 100.00% of 2.56 million checks (research plan G5). There is no price-minus-EMA
# indicator, so the EMA variant went with it (decided 2026-11-24).
_REGIME_FILTER_INDS: list[tuple[str, str, dict[str, Any]]] = [
    ("dist_50", "distance_from_moving_average", {"window": 50}),
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


def _above_floor(instance: tuple[str, str, dict[str, Any]]) -> bool:
    """No window / lookback under the research floor (MIN_RESEARCH_WINDOW_BARS)."""
    params = instance[2]
    return all(
        int(params[key]) >= MIN_RESEARCH_WINDOW_BARS
        for key in ("window", "lookback")
        if key in params
    )


# Research search floor (rotation step 5c-G): drop indicator instances that read fewer
# bars than the floor — they flip on 5-minute noise.
_ZERO_CENTERED = [i for i in _ZERO_CENTERED if _above_floor(i)]
_RATIO = [i for i in _RATIO if _above_floor(i)]
_CROSSOVER_FAMILIES = [
    family
    for family in ([i for i in f if _above_floor(i)] for f in _CROSSOVER_FAMILIES)
    if len(family) >= 2
]
_PRICE_COMPARISON_PAIRS = [
    pair for pair in _PRICE_COMPARISON_PAIRS if _above_floor(pair[0]) and _above_floor(pair[1])
]

# One aggregator for one-rule skeletons: with a single rule, voting (min 1 vote) and
# weighted_score (0.55) decided alike on every bar, 78 pairs of 78 (research plan G5),
# so the second copy only doubled the pool. Two-rule skeletons keep logical AND / OR.
_SINGLE_RULE_AGGS: list[dict[str, Any]] = [
    {"component": "voting", "parameters": {"min_votes": 1}},
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
    options = options or GenerationOptions()
    # `random` takes the first n distinct valid skeletons of the shuffled pool (at
    # least one, as before); grid and evolutionary take everything they are offered.
    limit = max(1, options.n_samples) if method == "random" else None
    if limit is not None:
        summary.requested_count = limit

    for template in _selected_templates(method=method, options=options):
        if limit is not None and summary.accepted_count >= limit:
            break
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

    if limit is not None and summary.accepted_count < limit:
        summary.exhausted = True
    return configs, summary


def _selected_templates(
    *,
    method: str,
    options: GenerationOptions,
) -> Iterator[dict[str, Any]]:
    templates = _templates()
    if method == "random":
        import random

        # The whole shuffled pool is offered; the caller stops at the number asked for,
        # so a skeleton that fails validation is replaced by the next one (plan G2).
        rng = random.Random(options.seed)
        shuffled = list(templates)
        rng.shuffle(shuffled)
        yield from shuffled
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

    Pattern counts after the research floor (tests pin them; 652 before plan G5's repair):
      threshold  :  78  (8 zero_centered × 3 variants × 3 filter options (none,
                         regime, volatility), less dist_50 gated by itself (3),
                         less the at-zero rule of mom_10, mom_20, dist_20 (9),
                         + 3 ratio × 2 variants × 3 filter options)
      crossover  :  28  (14 same-family pairs × with/without volume confirmation)
      comparison :  32  (28 zero_centered pairs less mom_N vs roc_N (2)
                         + 3 ratio pairs + 3 SMA-vs-EMA pairs)
      two-rule   : 252  (8 zero_centered × 3 variants, less the 3 at-zero twins,
                         × 3 ratio × 2 vol-variants × AND / OR)
      ─────────────────
      total pool : 390 skeletons
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
        for ind_id, ind_comp, ind_params in instances:
            variants = (
                _threshold_variants_for(ind_comp, ind_params)
                if domain == "zero_centered"
                else _THRESHOLD_VARIANTS[domain]
            )
            for vi, variant in enumerate(variants):
                if (
                    domain == "zero_centered"
                    and vi == 0
                    and _zero_rule_has_a_twin(ind_comp, ind_params)
                ):
                    continue
                for agg in _SINGLE_RULE_AGGS:
                    agg_tag = "voti" if agg["component"] == "voting" else "weig"
                    for ftag, finstance in filter_opts:
                        if finstance is not None and finstance[0] == ind_id:
                            # Gated by itself (dist_50 > 0 on a dist_50 rule): the
                            # unfiltered skeleton already covers it.
                            continue
                        indicators = [_ind(ind_id, ind_comp, **ind_params)]
                        filters: list[dict[str, Any]] = []

                        if finstance is not None:
                            fid, fcomp, fparams = finstance
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
    for a, b in combinations(_ZERO_CENTERED, 2):
        if _is_same_sign_comparison(a, b):
            continue
        (a_id, a_comp, a_p), (b_id, b_comp, b_p) = a, b
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
        for vi, variant in enumerate(_threshold_variants_for(pri_comp, pri_p)):
            if vi == 0 and _zero_rule_has_a_twin(pri_comp, pri_p):
                continue
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
