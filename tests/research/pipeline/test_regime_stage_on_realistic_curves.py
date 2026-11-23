"""The regime stage on curves of realistic size (research plans R7 and R15).

Five-minute equity curves over 60 sessions with a full-window Sharpe between -2
and 3, judged with the thresholds the research hook gives the regime gate. The
real RegimeStage runs: labels joined onto the curve, metrics per regime, the gate.

The labels here are dealt to random sessions, twenty sessions to each of three
labels in each dimension. Such labels know nothing about the strategy, so
whatever the gate finds with them is chance. That is the point of the last two
tests: on 60 sessions the gate finds something against a Sharpe-2.3 strategy
more often than not, which is why it reports and does not eliminate (R15), and
asking each label for about 100 sessions leaves nothing to judge on 60.

Run with:
    python -m pytest tests/research/pipeline/test_regime_stage_on_realistic_curves.py -v
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_DAY
from autonomous_trading_platform.research.pipeline.gates.regime_gate import (
    RegimeGateConfig,
    RegimeGateVerdict,
)
from autonomous_trading_platform.research.pipeline.gates.regime_labels import REGIME_COLUMNS
from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
from autonomous_trading_platform.research.pipeline.stages.regime_stage import (
    RegimeStage,
    RegimeStageConfig,
)
from tests.research.pipeline.monte_carlo_support import sharpe_runner, strategy, weekdays
from tests.research.pipeline.test_robustness_stages import _run

START, END = date(2024, 1, 2), date(2024, 3, 25)  # 60 weekdays
SESSIONS = 60
TREND = ("bull", "bear", "sideways")
VOLATILITY = ("low_volatility", "normal_volatility", "high_volatility")

# Six strategies by the Sharpe of their curve over the whole window.
SHARPE_OF = {
    "minus_2_0": -2.0,
    "minus_0_5": -0.5,
    "plus_0_5": 0.5,
    "plus_1_5": 1.5,
    "plus_2_3": 2.3,
    "plus_3_0": 3.0,
}
LOSERS = ["minus_2_0", "minus_0_5"]


class DealtLabels:
    """Each label of each dimension dealt to twenty sessions, at random from ``seed``."""

    def __init__(self, seed: int) -> None:
        self._seed = seed

    def load_daily_regimes(self, *, start_date: date, end_date: date, **_: object) -> pd.DataFrame:
        days = weekdays(start_date, end_date)
        rng = np.random.default_rng(self._seed)
        frame = pd.DataFrame({"date": days, **{c: [None] * len(days) for c in REGIME_COLUMNS}})
        frame["regime_trend"] = rng.permutation([TREND[i % 3] for i in range(len(days))])
        frame["regime_volatility"] = rng.permutation([VOLATILITY[i % 3] for i in range(len(days))])
        return frame


def _gate(**overrides: Any) -> RegimeGateConfig:
    """The research hook's regime gate, enforced unless a test says otherwise."""
    settings: dict[str, Any] = dict(
        dimensions=("trend", "volatility"),
        min_bars_per_regime=5 * BARS_PER_DAY,
        min_regime_sharpe=-1.0,
        max_regime_drawdown=-0.20,
        min_positive_regime_fraction=0.5,
        on_insufficient_coverage="pass",
        enforce=True,
    )
    settings.update(overrides)
    return RegimeGateConfig(**settings)


def _judge(sharpe_of: dict[str, float], deal: int, **gate: Any) -> StageResult:
    stage = RegimeStage(
        stage_config=RegimeStageConfig(
            name="regime", symbols=["SPY"], start_date=START, end_date=END, gate=_gate(**gate)
        ),
        simulation_runner=sharpe_runner(sharpe_of),  # type: ignore[arg-type]
        label_provider=DealtLabels(deal),  # type: ignore[arg-type]
    )
    return _run(stage, [strategy(name) for name in sharpe_of])


def _verdict(result: StageResult, name: str) -> RegimeGateVerdict:
    verdict = result.diagnostics[name].regime_verdict
    assert verdict is not None
    return verdict


def _measured(value: float | None) -> float:
    """A number the gate records only for a dimension it judged."""
    assert value is not None
    return value


def _kept(result: StageResult) -> list[str]:
    return [config.strategy_id for config in result.survivors]


def test_the_window_is_sixty_sessions_of_five_minute_bars() -> None:
    assert len(weekdays(START, END)) == SESSIONS
    assert BARS_PER_DAY == 78

    profile = _judge({"plus_2_3": 2.3}, deal=1).diagnostics["plus_2_3"].regime_profile

    assert profile is not None
    bars = {label: profile.by_trend.metrics_by_label[label].bar_count for label in TREND}
    # Twenty sessions to a label, give or take the one bar that opens the curve.
    assert all(20 * BARS_PER_DAY - 1 <= count <= 20 * BARS_PER_DAY for count in bars.values())


def test_the_gate_passes_some_and_fails_some_and_the_labels_decide_which() -> None:
    one_deal = _judge(SHARPE_OF, deal=5)
    another_deal = _judge(SHARPE_OF, deal=6)

    # Same six curves, same thresholds; only the labels moved.
    assert _kept(one_deal) == ["plus_2_3", "plus_3_0"]
    assert _kept(another_deal) == []

    # A finding names a regime, and that regime's numbers bear it out.
    failed = _verdict(another_deal, "plus_2_3")
    assert failed.passed is False
    assert failed.findings
    assert failed.failure_reasons == failed.findings
    too_low = [check for check in failed.dimension_checks if _measured(check.worst_sharpe) < -1.0]
    for check in too_low:
        named = f"{check.dimension}:{check.worst_label} Sharpe -"
        assert any(reason.startswith(named) for reason in failed.findings), named
    assert too_low or any("profitable in" in reason for reason in failed.findings)

    passed = _verdict(one_deal, "plus_2_3")
    assert passed.passed is True
    assert passed.findings == ()
    for check in passed.dimension_checks:
        assert _measured(check.worst_sharpe) >= -1.0
        assert _measured(check.positive_fraction) >= 0.5


def test_a_losing_strategy_is_found_out_under_every_deal() -> None:
    losers = {name: SHARPE_OF[name] for name in LOSERS}

    for deal in range(10):
        result = _judge(losers, deal)
        assert _kept(result) == [], deal
        for name in LOSERS:
            assert _verdict(result, name).findings, (name, deal)


def test_random_labels_find_against_a_good_strategy_more_often_than_not() -> None:
    """Why the gate reports only on a short window (R15).

    The labels are noise, so every finding below is chance. A regime here holds
    twenty sessions, and an annualised Sharpe over twenty sessions has a standard
    error near 3.6, so a strategy at 2.3 lands under the -1.0 floor in one of
    six regimes more often than not. Measured 2026-11-22 over 100 judgments
    (five curves, twenty deals): 70 with a finding, between 10 and 17 of 20 for
    a single curve. The eight deals below give 31 of 40.
    """
    good = {f"good_{letter}": 2.3 for letter in "abcde"}  # same Sharpe, different good days
    deals = range(8)

    results = [_judge(good, deal) for deal in deals]
    found_against = [
        (deal, name)
        for deal, result in zip(deals, results, strict=True)
        for name in good
        if _verdict(result, name).findings
    ]

    judgments = len(good) * len(deals)
    assert judgments // 2 <= len(found_against) < judgments  # chance: often, not always

    # The hook's setting, report only, on a deal with findings: recorded, kept.
    deal_with_findings, name = found_against[0]
    reported = _judge(good, deal_with_findings, enforce=False)
    assert _kept(reported) == list(good)
    verdict = _verdict(reported, name)
    assert verdict.findings
    assert verdict.passed is True
    assert verdict.failure_reasons == ()


def test_asking_a_hundred_sessions_of_each_label_leaves_nothing_to_judge_on_sixty() -> None:
    """The prerequisite R15 names before the gate may be enforced."""
    result = _judge(SHARPE_OF, deal=1, min_bars_per_regime=100 * BARS_PER_DAY)

    assert _kept(result) == list(SHARPE_OF)
    for name in SHARPE_OF:
        verdict = _verdict(result, name)
        assert verdict.findings == ()
        assert [check.evaluated for check in verdict.dimension_checks] == [False, False]
        assert any("insufficient regime coverage" in warning for warning in verdict.warnings)


def test_the_profitable_regimes_rule_speaks_once_the_sharpe_floor_is_out_of_the_way() -> None:
    """A losing curve trips the Sharpe floor first; with no floor, the other rule is heard."""
    loser = {"minus_2_0": -2.0}

    no_floor = _judge(loser, deal=1, min_regime_sharpe=-1e9)
    verdict = _verdict(no_floor, "minus_2_0")

    assert _kept(no_floor) == []
    assert verdict.findings
    assert all("profitable in" in reason for reason in verdict.findings)
    assert any(_measured(check.positive_fraction) < 0.5 for check in verdict.dimension_checks)

    neither_rule = _judge(loser, deal=1, min_regime_sharpe=-1e9, min_positive_regime_fraction=0.0)
    assert _kept(neither_rule) == ["minus_2_0"]
    assert _verdict(neither_rule, "minus_2_0").findings == ()
