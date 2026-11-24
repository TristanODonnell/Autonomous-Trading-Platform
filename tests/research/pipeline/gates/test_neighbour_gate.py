"""The neighbour gate's rules (plan R13): which neighbours hold, which settings are
fragile, and that a report-only gate eliminates nobody."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.pipeline.gates.neighbour_gate import (
    NeighbourGateConfig,
    NeighbourGateVerdict,
    NeighbourResult,
    SkippedNeighbour,
    evaluate_neighbour_gate,
)

REPORT_ONLY = NeighbourGateConfig()
ENFORCED = NeighbourGateConfig(enforce=True)


def _result(
    setting: str,
    direction: str,
    sharpe: float,
    *,
    trades: int = 40,
    base: float = 20,
    to: float = 0,
) -> NeighbourResult:
    value = to or (base * 0.8 if direction == "down" else base * 1.2)
    return NeighbourResult(
        setting=setting,
        direction=direction,
        base_value=base,
        value=value,
        strategy_id=f"{setting}_{direction}",
        sharpe=sharpe,
        max_drawdown=-0.05,
        total_return=0.04,
        trade_count=trades,
    )


def _skipped(
    setting: str, direction: str, reason: str = "above the declared maximum 60"
) -> SkippedNeighbour:
    return SkippedNeighbour(
        setting=setting, direction=direction, base_value=60, value=72, reason=reason
    )


def _judge(
    results: list[NeighbourResult],
    *,
    own: float | None = 2.0,
    skipped: list[SkippedNeighbour] | None = None,
    missing: int = 0,
    config: NeighbourGateConfig = REPORT_ONLY,
) -> NeighbourGateVerdict:
    return evaluate_neighbour_gate(
        strategy_id="s1",
        own_sharpe=own,
        results=results,
        skipped=skipped or [],
        missing=missing,
        config=config,
    )


def test_a_neighbour_holds_at_half_the_strategys_own_sharpe() -> None:
    verdict = _judge([_result("window", "down", 1.0), _result("window", "up", 0.99)], own=2.0)

    assert verdict.sharpe_needed == 1.0
    assert [result.holds for result in verdict.results] == [True, False]
    assert verdict.holding_fraction == 0.5


def test_a_neighbour_is_judged_on_its_level_and_not_on_how_alike_the_neighbours_are() -> None:
    """Four neighbours within 0.02 of each other, all losing: as stable as can be, and
    every setting fragile."""
    alike = [
        _result("window", "down", -1.00),
        _result("window", "up", -1.01),
        _result("threshold", "down", -0.99),
        _result("threshold", "up", -1.02),
    ]

    verdict = _judge(alike, own=2.0)

    assert verdict.holding_fraction == 0.0
    assert [check.fragile for check in verdict.settings] == [True, True]
    assert len(verdict.findings) == 2


def test_a_setting_is_fragile_when_neither_neighbour_holds() -> None:
    verdict = _judge(
        [
            _result("window", "down", -0.31, to=16),
            _result("window", "up", 0.12, to=24),
            _result("threshold", "down", 1.8),
            _result("threshold", "up", 0.2),
        ],
        own=1.57,
    )

    assert {check.setting: check.fragile for check in verdict.settings} == {
        "window": True,
        "threshold": False,
    }
    assert verdict.findings == (
        "window = 20: neither neighbour holds (at 16: Sharpe -0.31; at 24: Sharpe 0.12; "
        "own Sharpe 1.57, a neighbour needs 0.79)",
    )


def test_one_neighbour_holding_is_enough_for_the_setting() -> None:
    verdict = _judge([_result("window", "down", -3.0), _result("window", "up", 1.9)])

    (check,) = verdict.settings
    assert (check.neighbours_run, check.neighbours_holding, check.fragile) == (2, 1, False)
    assert verdict.findings == ()


def test_a_setting_with_one_neighbour_run_is_judged_on_that_one_and_the_finding_says_so() -> None:
    verdict = _judge(
        [_result("lookback", "down", 0.1, base=60, to=48)],
        skipped=[_skipped("lookback", "up")],
    )

    (check,) = verdict.settings
    assert (check.neighbours_run, check.fragile) == (1, True)
    (finding,) = verdict.findings
    assert finding.startswith("lookback = 60: its one neighbour run does not hold (at 48: ")
    assert verdict.skipped == (_skipped("lookback", "up"),)


def test_a_neighbour_that_did_not_trade_does_not_hold() -> None:
    """A flat curve has a Sharpe of 0.0, which would meet a floor of 0.0."""
    verdict = _judge(
        [_result("window", "down", 0.0, trades=0, to=16), _result("window", "up", 0.0, trades=0)],
        own=0.0,
    )

    assert verdict.sharpe_needed == 0.0
    assert [result.holds for result in verdict.results] == [False, False]
    assert "at 16: no trades" in verdict.findings[0]


def test_a_losing_strategys_neighbours_are_held_to_the_floor() -> None:
    """Half of a negative Sharpe is a lower bar than none: the floor stands in."""
    verdict = _judge([_result("window", "down", -0.5), _result("window", "up", 0.1)], own=-2.0)

    assert verdict.sharpe_needed == 0.0
    assert [result.holds for result in verdict.results] == [False, True]


def test_the_two_thresholds_are_the_configs() -> None:
    results = [_result("window", "down", 1.5), _result("window", "up", 0.5)]

    strict = _judge(results, own=2.0, config=NeighbourGateConfig(min_share_of_own_sharpe=0.9))
    floor = _judge(results, own=0.4, config=NeighbourGateConfig(min_neighbour_sharpe=1.0))

    assert strict.sharpe_needed == pytest.approx(1.8)
    assert [result.holds for result in strict.results] == [False, False]
    assert floor.sharpe_needed == 1.0
    assert [result.holds for result in floor.results] == [True, False]


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_the_share_of_the_own_sharpe_is_a_share(bad: float) -> None:
    with pytest.raises(ValueError, match="min_share_of_own_sharpe"):
        NeighbourGateConfig(min_share_of_own_sharpe=bad)


# --------------------------------------------------------------------------------------
# Report only, and enforced
# --------------------------------------------------------------------------------------

FRAGILE = [_result("window", "down", -1.0), _result("window", "up", -1.0)]


def test_report_only_records_the_finding_and_passes_the_strategy() -> None:
    verdict = _judge(FRAGILE)

    assert verdict.passed is True and verdict.enforced is False
    assert len(verdict.findings) == 1
    assert verdict.failure_reasons == ()


def test_enforced_the_finding_eliminates() -> None:
    verdict = _judge(FRAGILE, config=ENFORCED)

    assert verdict.passed is False and verdict.enforced is True
    assert verdict.failure_reasons == verdict.findings != ()


def test_enforced_a_strategy_with_no_fragile_setting_passes() -> None:
    verdict = _judge(
        [_result("window", "down", 1.5), _result("window", "up", 1.7)], config=ENFORCED
    )

    assert verdict.passed is True and verdict.findings == ()


# --------------------------------------------------------------------------------------
# What the gate could not see
# --------------------------------------------------------------------------------------


def test_without_a_run_of_the_strategy_itself_the_neighbours_are_recorded_not_judged() -> None:
    verdict = _judge(FRAGILE, own=None, config=ENFORCED)

    assert verdict.passed is True
    assert verdict.sharpe_needed is None and verdict.holding_fraction is None
    assert [result.holds for result in verdict.results] == [None, None]
    assert verdict.findings == ()
    assert verdict.warnings == (
        "no run of the strategy itself over these days: neighbours recorded, not judged",
    )
    # Their numbers are kept all the same.
    assert verdict.worst_neighbour_sharpe == -1.0


def test_a_strategy_with_no_neighbour_run_passes_and_says_nothing_was_checked() -> None:
    verdict = _judge(
        [], skipped=[_skipped("lookback", "down"), _skipped("lookback", "up")], config=ENFORCED
    )

    assert verdict.passed is True and verdict.findings == ()
    assert verdict.warnings == ("no neighbour was run: nothing checked (2 could not be built)",)
    (check,) = verdict.settings
    # Not checked is not fragile.
    assert (check.neighbours_run, check.fragile) == (0, False)
    assert verdict.holding_fraction is None and verdict.median_neighbour_sharpe is None


def test_a_neighbour_asked_for_that_gave_no_result_is_a_finding() -> None:
    """As the stress gate has it: the gate does not pass on evidence it asked for and
    did not get."""
    report_only = _judge([_result("window", "up", 1.9)], missing=1)
    enforced = _judge([_result("window", "up", 1.9)], missing=1, config=ENFORCED)

    assert report_only.findings == ("no result for 1 neighbour run(s) that were asked for",)
    assert report_only.passed is True
    assert enforced.passed is False


def test_the_summary_numbers() -> None:
    verdict = _judge(
        [
            _result("window", "down", 0.4),
            _result("window", "up", 1.6),
            _result("threshold", "down", 2.2),
            _result("threshold", "up", float("nan")),
        ],
        own=2.0,
    )

    assert verdict.own_sharpe == 2.0
    # A Sharpe that is not a number does not hold and is left out of the summary.
    assert [result.holds for result in verdict.results] == [False, True, True, False]
    assert verdict.holding_fraction == 0.5
    assert verdict.median_neighbour_sharpe == 1.6
    assert verdict.worst_neighbour_sharpe == 0.4


def test_settings_keep_the_order_they_were_run_in_with_the_unchecked_last() -> None:
    verdict = _judge(
        [_result("b", "down", 1.5), _result("a", "up", 1.5)],
        skipped=[_skipped("z", "down"), _skipped("b", "up")],
    )

    assert [check.setting for check in verdict.settings] == ["b", "a", "z"]
