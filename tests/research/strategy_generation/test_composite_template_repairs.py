"""The composite templates' three repairs (research plan G5, Step 1b row 2).

G5 measured the 652 grid skeletons on real bars and found 324 behaviours. Three causes,
each repaired where the skeletons are made and each pinned here:

- the regime filter read a price average against zero, which every price passes; it now
  reads price minus its average (`dist_50 > 0`);
- rate_of_change (a fraction) was given the +-1.5 / +-2.0 meant for z-scores, which no
  bar reaches; its bands are now measured fractions per lookback;
- one-rule skeletons came twice, under voting and weighted_score, which decide alike
  with one rule; they now come once.

The behaviour tests run skeletons from the pool through the real composite strategy.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.research.strategy_generation import composite_generation as cg
from autonomous_trading_platform.strategy.composite import CompositeRuleStrategy
from tests.strategy.composite.test_composite_rule_strategy import _make_bars, _make_context


def _pool() -> dict[str, dict[str, Any]]:
    return {s["metadata"]["generation_template"]: s for s in cg._build_skeletons()}


def _direction(template: dict[str, Any], closes: list[float]) -> SignalDirection | None:
    strategy = CompositeRuleStrategy("g5-test", template)
    signal = strategy.evaluate_symbol(_make_context(_make_bars(closes)))
    return None if signal is None else signal.direction


# -- the pool --------------------------------------------------------------------------


def test_the_pool_has_437_skeletons_by_kind() -> None:
    # The size of task 4's grid run; 652 before the repair (240 / 56 / 68 / 288).
    kinds = Counter(name.split("_")[0] for name in _pool())
    assert kinds == {"thresh": 87, "xover": 28, "cmp": 34, "logic": 288}


def test_every_regime_filter_reads_price_minus_its_average() -> None:
    gated = 0
    for name, skeleton in _pool().items():
        components = {i["id"]: i["component"] for i in skeleton["indicators"]}
        for f in skeleton["filters"]:
            if f["component"] != "regime_filter":
                continue
            gated += 1
            assert components[f["input"]["indicator_id"]] == "distance_from_moving_average", name
            assert (f["operator"], f["threshold"]) == ("gt", 0.0), name
    assert gated > 0


def test_no_skeleton_is_gated_by_its_own_rule_indicator() -> None:
    for name, skeleton in _pool().items():
        rule_inputs = {
            ref["indicator_id"]
            for rule in skeleton["entry_rules"]
            for ref in rule["inputs"].values()
        }
        for f in skeleton["filters"]:
            assert f["input"]["indicator_id"] not in rule_inputs, name


def test_one_rule_skeletons_come_under_one_aggregator() -> None:
    for name, skeleton in _pool().items():
        if len(skeleton["entry_rules"]) == 1:
            assert skeleton["aggregator"]["component"] == "voting", name


def test_rate_of_change_rules_use_the_measured_bands() -> None:
    lookback_of = {}
    for skeleton in _pool().values():
        for i in skeleton["indicators"]:
            if i["component"] == "rate_of_change":
                lookback_of[i["id"]] = i["parameters"]["lookback"]
    assert set(lookback_of.values()) == set(cg._ROC_MEAN_REVERSION_BANDS)

    seen = 0
    for name, skeleton in _pool().items():
        for rule in skeleton["entry_rules"]:
            ind = rule["inputs"]["value"]["indicator_id"] if "value" in rule["inputs"] else None
            if ind not in lookback_of:
                continue
            seen += 1
            mild, strong = cg._ROC_MEAN_REVERSION_BANDS[lookback_of[ind]]
            levels = {abs(v) for k, v in rule["parameters"].items() if k != "confidence"}
            assert levels <= {0.0, mild, strong}, name
            assert all(level < 0.1 for level in levels), name  # a fraction, not 1.5
    assert seen > 0


def test_a_rate_of_change_lookback_without_a_measured_band_is_refused() -> None:
    with pytest.raises(ValueError, match="no measured band"):
        cg._threshold_variants_for("rate_of_change", {"lookback": 15})


def test_other_zero_centered_indicators_keep_their_variants() -> None:
    # momentum and distance thresholds are in dollars: recorded in G5, not changed here.
    for component in ("momentum", "z_score", "distance_from_moving_average"):
        assert (
            cg._threshold_variants_for(component, {"window": 20, "lookback": 20})
            == (cg._THRESHOLD_VARIANTS["zero_centered"])
        )


# -- behaviour ------------------------------------------------------------------------

# Falls from 200 to 100 over 60 bars, then ticks up for 20: the last close is above its
# 20-bar mean (z > 0, so the trend rule says buy) and well below its 50-bar average.
_BELOW_ITS_AVERAGE = [200 - (100 * i / 59) for i in range(60)] + [100 + 0.25 * i for i in range(20)]


def test_the_regime_gate_blocks_a_buy_while_price_is_below_its_average() -> None:
    pool = _pool()
    assert _direction(pool["thresh_z_20_v0_voti"], _BELOW_ITS_AVERAGE) == SignalDirection.BUY
    gated = _direction(pool["thresh_z_20_v0_voti_freg_dist_50"], _BELOW_ITS_AVERAGE)
    assert gated != SignalDirection.BUY


def test_the_regime_gate_lets_a_buy_through_while_price_is_above_its_average() -> None:
    rising = [100 + 0.5 * i for i in range(80)]
    assert _direction(_pool()["thresh_z_20_v0_voti_freg_dist_50"], rising) == SignalDirection.BUY


def test_a_two_percent_fall_fires_rate_of_changes_mild_band() -> None:
    # Flat at 100, then down 2% over the last 10 bars: roc_10 = -0.02, past the mild
    # band (buy_below -0.015). Under the old +-1.5 this needed a 150% fall.
    closes = [100.0] * 60 + [100 - 0.2 * (i + 1) for i in range(10)]
    pool = _pool()
    assert _direction(pool["thresh_roc_10_v1_voti"], closes) == SignalDirection.BUY
    # A 1% fall is inside the mild band: no buy.
    small = [100.0] * 60 + [100 - 0.1 * (i + 1) for i in range(10)]
    assert _direction(pool["thresh_roc_10_v1_voti"], small) != SignalDirection.BUY
