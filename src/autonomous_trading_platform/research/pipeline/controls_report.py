"""The planted controls and their scorecard (research plan, top board "Planted controls, M1").

Decided by the user 2026-11-25. The pipeline is run on strategies whose answer is known:
the noisy oracle (``strategy/implementations/planted_oracle_strategy.py``) at three
strengths, which must be found, and at accuracy 0.5 under five seeds, which is noise and
must be rejected. The expected outcomes were fixed before any control ran (decision C4)
and are written once, in ``PLANTED_CONTROLS``: the research hook takes its candidates
from this table and this module scores the run against the same table.

The rules:

- ``must_be_found`` (accuracy 0.60 and 0.55): passes every stage and beats buy and hold
  of the names in force on the holdout (excess return above 0). If not, the pipeline
  cannot see an edge of that size, and a "nothing found" of it says little.
- ``report_only`` (accuracy 0.52): no expectation; where the detection floor sits.
- ``must_be_rejected`` (accuracy 0.50): does not pass every stage. Each stage's pass rate
  over the noise seeds is reported as well: the share of no-edge strategies a gate lets
  through.

Report only: it reads the gate report and the holdout report and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLANTED_ORACLE_TYPE = "planted_oracle"

ROLE_MUST_BE_FOUND = "must_be_found"
ROLE_REPORT_ONLY = "report_only"
ROLE_MUST_BE_REJECTED = "must_be_rejected"

_EXPECTED = {
    ROLE_MUST_BE_FOUND: "passes every stage and beats buy and hold on the holdout",
    ROLE_REPORT_ONLY: "no expectation (the sensitivity probe)",
    ROLE_MUST_BE_REJECTED: "does not pass every stage",
}


@dataclass(frozen=True, slots=True)
class PlantedControl:
    accuracy: float
    seed: int
    role: str

    @property
    def strategy_id(self) -> str:
        return f"{PLANTED_ORACLE_TYPE}__acc{round(self.accuracy * 100):d}_seed{self.seed}"

    def strategy_config(self) -> dict[str, Any]:
        """The control as an entry of an experiment's fixed ``strategy_set``."""
        return {
            "type": PLANTED_ORACLE_TYPE,
            "strategy_id": self.strategy_id,
            "parameters": {"accuracy": self.accuracy, "seed": self.seed},
        }


# The three oracles share a seed, so they are nested: on any session the 0.60 oracle
# calls right whenever the 0.55 one does. The noise seeds are their own.
PLANTED_CONTROLS: tuple[PlantedControl, ...] = (
    PlantedControl(0.60, 1, ROLE_MUST_BE_FOUND),
    PlantedControl(0.55, 1, ROLE_MUST_BE_FOUND),
    PlantedControl(0.52, 1, ROLE_REPORT_ONLY),
    *(PlantedControl(0.50, seed, ROLE_MUST_BE_REJECTED) for seed in range(11, 16)),
)


def planted_control_strategy_set(
    controls: tuple[PlantedControl, ...] = PLANTED_CONTROLS,
) -> list[dict[str, Any]]:
    return [control.strategy_config() for control in controls]


def score_controls(
    *,
    gate_report: dict[str, Any],
    holdout_report: dict[str, Any] | None,
    controls: tuple[PlantedControl, ...] = PLANTED_CONTROLS,
) -> dict[str, Any]:
    """Each control against its expected outcome, and the two answers M1 asks for."""
    if gate_report.get("not_built"):
        return {"not_scored": f"gate report not built: {gate_report['not_built']}"}

    records = {record["strategy_id"]: record for record in gate_report.get("strategies", [])}
    holdout = {
        candidate["strategy_id"]: candidate
        for candidate in (holdout_report or {}).get("candidates") or []
    }

    rows = [_score_one(control, records.get(control.strategy_id), holdout) for control in controls]
    must_find = [row for row in rows if row["role"] == ROLE_MUST_BE_FOUND]
    noise = [row for row in rows if row["role"] == ROLE_MUST_BE_REJECTED]

    return {
        "controls": rows,
        "planted_edge_found": bool(must_find) and all(row["met"] is True for row in must_find),
        "noise_rejected": bool(noise) and all(row["met"] is True for row in noise),
        "noise_passed_every_stage": sum(1 for row in noise if row["passed_every_stage"]),
        "noise_seeds": len(noise),
        "noise_pass_rate_by_stage": _noise_pass_rates(
            gate_report.get("stages", []), [records.get(row["strategy_id"]) for row in noise]
        ),
    }


def _score_one(
    control: PlantedControl,
    record: dict[str, Any] | None,
    holdout: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "strategy_id": control.strategy_id,
        "accuracy": control.accuracy,
        "seed": control.seed,
        "role": control.role,
        "expected": _EXPECTED[control.role],
        "passed_every_stage": False,
        "eliminated_at": None,
        "holdout_return": None,
        "holdout_excess_return": None,
    }
    if record is None:
        row["met"] = None if control.role == ROLE_REPORT_ONLY else False
        row["why"] = "not in the gate report: the control did not enter the pipeline"
        return row

    row["passed_every_stage"] = bool(record.get("passed_every_stage"))
    row["eliminated_at"] = record.get("eliminated_at")
    versus = (holdout.get(control.strategy_id) or {}).get("versus_benchmark")
    if isinstance(versus, dict):
        row["holdout_return"] = versus.get("return")
        row["holdout_excess_return"] = versus.get("excess_return")

    if control.role == ROLE_REPORT_ONLY:
        row["met"] = None
        row["why"] = "report only"
    elif control.role == ROLE_MUST_BE_REJECTED:
        row["met"] = not row["passed_every_stage"]
        row["why"] = (
            f"eliminated at {row['eliminated_at']}"
            if row["met"]
            else "passed every stage: a gate let noise through"
        )
    else:
        excess = row["holdout_excess_return"]
        if not row["passed_every_stage"]:
            row["met"], row["why"] = False, f"eliminated at {row['eliminated_at']}"
        elif excess is None:
            row["met"], row["why"] = False, "passed every stage but has no holdout comparison"
        elif excess > 0:
            row["met"], row["why"] = True, "passed every stage and beat buy and hold"
        else:
            row["met"], row["why"] = False, "passed every stage, did not beat buy and hold"
    return row


def _noise_pass_rates(
    stages: list[str], noise_records: list[dict[str, Any] | None]
) -> dict[str, dict[str, int]]:
    """Per stage, how many noise seeds entered it and how many it passed."""
    rates: dict[str, dict[str, int]] = {stage: {"entered": 0, "passed": 0} for stage in stages}
    for record in noise_records:
        for entry in (record or {}).get("stages", []):
            counts = rates.setdefault(entry["stage"], {"entered": 0, "passed": 0})
            counts["entered"] += 1
            counts["passed"] += 1 if entry.get("passed") else 0
    return rates
