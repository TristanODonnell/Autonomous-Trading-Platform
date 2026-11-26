"""The planted controls' table and scorecard (research plan, "Planted controls, M1").

The table is the user's decision of 2026-11-25 (C4, outcomes fixed before any control
ran); these tests pin it, so a change to an expected outcome is a visible change here,
and they check each rule on hand-made gate and holdout reports.
"""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.research.pipeline.controls_report import (
    PLANTED_CONTROLS,
    ROLE_MUST_BE_FOUND,
    ROLE_MUST_BE_REJECTED,
    ROLE_REPORT_ONLY,
    PlantedControl,
    planted_control_strategy_set,
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
