"""The planted controls' table and scorecard (research plan, "Planted controls, M1").

The table is the user's decision of 2026-11-25 (C4, outcomes fixed before any control
ran); these tests pin it, so a change to an expected outcome is a visible change here,
and they check each rule on hand-made gate and holdout reports.
"""

from __future__ import annotations

from typing import Any

import pytest

from autonomous_trading_platform.research.pipeline.controls_report import (
    PLANTED_CONTROLS,
    ROLE_MUST_BE_FOUND,
    ROLE_MUST_BE_REJECTED,
    ROLE_REPORT_ONLY,
    PlantedControl,
    clopper_pearson_95,
    noise_sample_passes_allowed,
    planted_control_strategy_set,
    planted_controls_with_noise_sample,
    score_controls,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

_STAGES = ["initial_quality_filter", "walk_forward_robustness", "stress_robustness"]


def _record(strategy_id: str, eliminated_at: str | None) -> dict[str, Any]:
    stages = []
    for stage in _STAGES:
        passed = stage != eliminated_at
        stages.append({"stage": stage, "passed": passed})
        if not passed:
            break
    return {
        "strategy_id": strategy_id,
        "passed_every_stage": eliminated_at is None,
        "eliminated_at": eliminated_at,
        "stages": stages,
    }


def _holdout(*pairs: tuple[str, float]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "strategy_id": sid,
                "versus_benchmark": {"return": excess - 0.05, "excess_return": excess},
            }
            for sid, excess in pairs
        ]
    }


ORACLE_60 = PlantedControl(0.60, 1, ROLE_MUST_BE_FOUND)
ORACLE_52 = PlantedControl(0.52, 1, ROLE_REPORT_ONLY)
NOISE_A = PlantedControl(0.50, 11, ROLE_MUST_BE_REJECTED)
NOISE_B = PlantedControl(0.50, 12, ROLE_MUST_BE_REJECTED)
SMALL = (ORACLE_60, ORACLE_52, NOISE_A, NOISE_B)


def _row(report: dict[str, Any], control: PlantedControl) -> dict[str, Any]:
    return next(r for r in report["controls"] if r["strategy_id"] == control.strategy_id)


# --- the table --------------------------------------------------------------------------


def test_the_table_is_the_decided_one() -> None:
    assert [(c.accuracy, c.seed, c.role) for c in PLANTED_CONTROLS] == [
        (0.60, 1, ROLE_MUST_BE_FOUND),
        (0.55, 1, ROLE_MUST_BE_FOUND),
        (0.52, 1, ROLE_REPORT_ONLY),
        (0.50, 11, ROLE_MUST_BE_REJECTED),
        (0.50, 12, ROLE_MUST_BE_REJECTED),
        (0.50, 13, ROLE_MUST_BE_REJECTED),
        (0.50, 14, ROLE_MUST_BE_REJECTED),
        (0.50, 15, ROLE_MUST_BE_REJECTED),
    ]


def test_every_control_is_a_valid_config_with_its_own_id() -> None:
    configs = [StrategyConfig(**entry) for entry in planted_control_strategy_set()]

    assert len({c.strategy_id for c in configs}) == len(PLANTED_CONTROLS)
    assert configs[0].strategy_id == "planted_oracle__acc60_seed1"
    assert {c.type for c in configs} == {"planted_oracle"}
    assert configs[3].parameters == {"accuracy": 0.5, "seed": 11}


# --- the rules ---------------------------------------------------------------------------


def test_all_as_expected() -> None:
    report = score_controls(
        gate_report={
            "stages": _STAGES,
            "strategies": [
                _record(ORACLE_60.strategy_id, None),
                _record(ORACLE_52.strategy_id, "stress_robustness"),
                _record(NOISE_A.strategy_id, "initial_quality_filter"),
                _record(NOISE_B.strategy_id, "walk_forward_robustness"),
            ],
        },
        holdout_report=_holdout((ORACLE_60.strategy_id, 0.04)),
        controls=SMALL,
    )

    assert report["planted_edge_found"] is True
    assert report["noise_rejected"] is True
    assert _row(report, ORACLE_60)["met"] is True
    assert _row(report, ORACLE_60)["holdout_excess_return"] == 0.04
    assert _row(report, ORACLE_52)["met"] is None
    assert report["noise_passed_every_stage"] == 0
    assert report["noise_seeds"] == 2
    assert report["noise_pass_rate"]["rate"] == 0.0
    assert report["noise_pass_rate"]["low_95"] == 0.0
    assert report["noise_pass_rate_by_stage"] == {
        "initial_quality_filter": {"entered": 2, "passed": 1},
        "walk_forward_robustness": {"entered": 1, "passed": 0},
        "stress_robustness": {"entered": 0, "passed": 0},
    }


def test_an_oracle_that_passes_every_stage_but_trails_buy_and_hold_is_not_found() -> None:
    report = score_controls(
        gate_report={"stages": _STAGES, "strategies": [_record(ORACLE_60.strategy_id, None)]},
        holdout_report=_holdout((ORACLE_60.strategy_id, -0.01)),
        controls=(ORACLE_60,),
    )

    assert _row(report, ORACLE_60)["met"] is False
    assert report["planted_edge_found"] is False


def test_an_oracle_exactly_level_with_buy_and_hold_is_not_found() -> None:
    report = score_controls(
        gate_report={"stages": _STAGES, "strategies": [_record(ORACLE_60.strategy_id, None)]},
        holdout_report=_holdout((ORACLE_60.strategy_id, 0.0)),
        controls=(ORACLE_60,),
    )

    assert _row(report, ORACLE_60)["met"] is False


def test_an_eliminated_oracle_is_not_found_and_says_where() -> None:
    report = score_controls(
        gate_report={
            "stages": _STAGES,
            "strategies": [_record(ORACLE_60.strategy_id, "walk_forward_robustness")],
        },
        holdout_report=_holdout(),
        controls=(ORACLE_60,),
    )

    row = _row(report, ORACLE_60)
    assert row["met"] is False
    assert row["why"] == "eliminated at walk_forward_robustness"


def test_an_oracle_with_no_holdout_comparison_is_not_found() -> None:
    report = score_controls(
        gate_report={"stages": _STAGES, "strategies": [_record(ORACLE_60.strategy_id, None)]},
        holdout_report={"not_scored": "no holdout session yet", "candidates": None},
        controls=(ORACLE_60,),
    )

    assert _row(report, ORACLE_60)["met"] is False


def test_one_noise_seed_through_every_stage_fails_the_noise_rule() -> None:
    report = score_controls(
        gate_report={
            "stages": _STAGES,
            "strategies": [
                _record(NOISE_A.strategy_id, None),
                _record(NOISE_B.strategy_id, "initial_quality_filter"),
            ],
        },
        holdout_report=_holdout(),
        controls=(NOISE_A, NOISE_B),
    )

    assert report["noise_rejected"] is False
    assert report["noise_passed_every_stage"] == 1
    assert _row(report, NOISE_A)["why"] == "passed every stage: a gate let noise through"
    assert _row(report, NOISE_B)["met"] is True


def test_a_control_missing_from_the_gate_report_is_not_met() -> None:
    report = score_controls(
        gate_report={"stages": _STAGES, "strategies": []},
        holdout_report=None,
        controls=SMALL,
    )

    assert report["planted_edge_found"] is False
    assert report["noise_rejected"] is False
    assert _row(report, ORACLE_52)["met"] is None


def test_a_gate_report_that_was_not_built_scores_nothing() -> None:
    report = score_controls(
        gate_report={"stages": [], "strategies": [], "not_built": "boom"},
        holdout_report=None,
    )

    assert report == {"not_scored": "gate report not built: boom"}


# --- the noise sample (2026-11-26) -------------------------------------------------------


def test_the_noise_sample_keeps_the_oracles_and_draws_fresh_seeds() -> None:
    controls = planted_controls_with_noise_sample(40)

    oracles = [c for c in controls if c.role != ROLE_MUST_BE_REJECTED]
    noise = [c for c in controls if c.role == ROLE_MUST_BE_REJECTED]
    assert oracles == [c for c in PLANTED_CONTROLS if c.role != ROLE_MUST_BE_REJECTED]
    assert [c.seed for c in noise] == list(range(101, 141))
    assert {c.accuracy for c in noise} == {0.50}
    # None of the five seeds already seen (one of them passed every stage).
    seen = {c.seed for c in PLANTED_CONTROLS if c.role == ROLE_MUST_BE_REJECTED}
    assert not seen & {c.seed for c in noise}
    assert len({c.strategy_id for c in controls}) == 43


@pytest.mark.parametrize("bad", [0, -1, 201, True, 1.5, "40", None])
def test_a_bad_noise_sample_size_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="noise_seeds must be"):
        planted_controls_with_noise_sample(bad)  # type: ignore[arg-type]


def test_the_noise_pass_rate_is_reported_with_its_95_percent_range() -> None:
    noise = planted_controls_with_noise_sample(4)[3:]
    report = score_controls(
        gate_report={
            "stages": _STAGES,
            "strategies": [
                _record(noise[0].strategy_id, None),
                *(_record(c.strategy_id, "initial_quality_filter") for c in noise[1:]),
            ],
        },
        holdout_report=_holdout(),
        controls=noise,
    )

    assert report["noise_passed_every_stage"] == 1
    rate = report["noise_pass_rate"]
    assert rate["rate"] == 0.25
    assert rate["low_95"] == pytest.approx(0.00631, abs=1e-4)
    assert rate["high_95"] == pytest.approx(0.80588, abs=1e-4)


@pytest.mark.parametrize(
    ("successes", "trials", "low", "high"),
    [
        # Exact Clopper-Pearson values, from scipy.stats.beta.ppf (2026-11-26).
        (1, 5, 0.005051, 0.716418),
        (0, 5, 0.0, 0.521824),
        (5, 5, 0.478176, 1.0),
        (2, 40, 0.006114, 0.169197),
        (10, 40, 0.126915, 0.411962),
        (0, 40, 0.0, 0.088097),
    ],
)
def test_the_95_percent_range_is_the_exact_one(
    successes: int, trials: int, low: float, high: float
) -> None:
    got_low, got_high = clopper_pearson_95(successes, trials)
    assert got_low == pytest.approx(low, abs=1e-5)
    assert got_high == pytest.approx(high, abs=1e-5)


def test_no_trials_has_no_range() -> None:
    assert clopper_pearson_95(0, 0) == (None, None)


def test_the_bar_is_the_decided_one() -> None:
    """User, 2026-11-26, before the run: at most 2 of 40 noise controls may pass."""
    assert noise_sample_passes_allowed(40) == 2
    assert noise_sample_passes_allowed(5) == 0
    assert noise_sample_passes_allowed(100) == 5


@pytest.mark.parametrize(("passed", "within"), [(0, True), (2, True), (3, False)])
def test_a_noise_sample_is_judged_against_its_bar(passed: int, within: bool) -> None:
    noise = planted_controls_with_noise_sample(40)[3:]
    report = score_controls(
        gate_report={
            "stages": _STAGES,
            "strategies": [
                _record(c.strategy_id, None if i < passed else "initial_quality_filter")
                for i, c in enumerate(noise)
            ],
        },
        holdout_report=_holdout(),
        controls=noise,
        noise_passes_allowed=noise_sample_passes_allowed(40),
    )

    assert report["noise_passes_allowed"] == 2
    assert report["noise_within_bar"] is within


def test_the_fixed_table_has_no_bar() -> None:
    report = score_controls(gate_report={"stages": _STAGES, "strategies": []}, holdout_report=None)
    assert report["noise_passes_allowed"] is None
    assert report["noise_within_bar"] is None


def test_a_later_sample_starts_where_it_is_told() -> None:
    controls = planted_controls_with_noise_sample(40, first_seed=141)

    noise = [c for c in controls if c.role == ROLE_MUST_BE_REJECTED]
    assert [c.seed for c in noise] == list(range(141, 181))
    assert not {c.seed for c in noise} & set(range(101, 141))


@pytest.mark.parametrize("bad", [100, 15, True, "141", 141.0])
def test_a_first_seed_among_the_fixed_table_or_not_an_integer_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="noise_first_seed must be"):
        planted_controls_with_noise_sample(40, first_seed=bad)  # type: ignore[arg-type]
