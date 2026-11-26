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

A noise sample (option ``noise_seeds``, added 2026-11-26 after the first controls run let
one noise seed of five through every stage): the same three oracles and N noise controls
on fresh seeds (101 on, never the five already seen), so the share of no-edge strategies
the gates pass is measured on a sample large enough to read. The summary gives that share
with its 95% range.

Report only: it reads the gate report and the holdout report and changes nothing.
"""

from __future__ import annotations

import math
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


# The noise sample's seeds start here, clear of the five seeds above (11 to 15), whose
# results were seen before the sample was drawn: reusing them would count a known pass.
NOISE_SAMPLE_FIRST_SEED = 101
NOISE_SAMPLE_MAX = 200

# The bar for a noise sample, fixed by the user 2026-11-26 before the 40-seed run: at
# most 5% of the noise controls may pass every stage (2 of 40); more, and the gates are
# tightened before G10.
NOISE_SAMPLE_PASS_RATE_ALLOWED = 0.05


def noise_sample_passes_allowed(noise_seeds: int) -> int:
    """How many of a noise sample may pass every stage under the user's bar."""
    return math.floor(NOISE_SAMPLE_PASS_RATE_ALLOWED * noise_seeds + 1e-9)


def planted_controls_with_noise_sample(noise_seeds: int) -> tuple[PlantedControl, ...]:
    """The three oracles of ``PLANTED_CONTROLS`` and ``noise_seeds`` noise controls on
    fresh seeds. The oracles stay in as a check on the run itself: if they stopped
    passing, the run broke, not the gates."""
    if isinstance(noise_seeds, bool) or not isinstance(noise_seeds, int):
        raise ValueError(f"noise_seeds must be an integer, got {noise_seeds!r}")
    if not 1 <= noise_seeds <= NOISE_SAMPLE_MAX:
        raise ValueError(f"noise_seeds must be 1 to {NOISE_SAMPLE_MAX}, got {noise_seeds}")
    oracles = tuple(c for c in PLANTED_CONTROLS if c.role != ROLE_MUST_BE_REJECTED)
    noise = tuple(
        PlantedControl(0.50, NOISE_SAMPLE_FIRST_SEED + i, ROLE_MUST_BE_REJECTED)
        for i in range(noise_seeds)
    )
    return oracles + noise


def planted_control_strategy_set(
    controls: tuple[PlantedControl, ...] = PLANTED_CONTROLS,
) -> list[dict[str, Any]]:
    return [control.strategy_config() for control in controls]


def score_controls(
    *,
    gate_report: dict[str, Any],
    holdout_report: dict[str, Any] | None,
    controls: tuple[PlantedControl, ...] = PLANTED_CONTROLS,
    noise_passes_allowed: int | None = None,
) -> dict[str, Any]:
    """Each control against its expected outcome, and the two answers M1 asks for.

    ``noise_passes_allowed``: a noise sample's bar (``noise_sample_passes_allowed``);
    the summary then says whether the sample stayed within it. None for the fixed
    table, whose rule is that no noise seed passes."""
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
    noise_passed = sum(1 for row in noise if row["passed_every_stage"])
    low, high = clopper_pearson_95(noise_passed, len(noise))

    return {
        "controls": rows,
        "planted_edge_found": bool(must_find) and all(row["met"] is True for row in must_find),
        "noise_rejected": bool(noise) and all(row["met"] is True for row in noise),
        "noise_passed_every_stage": noise_passed,
        "noise_seeds": len(noise),
        # The gates' false-pass rate over the noise seeds and its exact 95% range.
        "noise_pass_rate": {
            "rate": noise_passed / len(noise) if noise else None,
            "low_95": low,
            "high_95": high,
        },
        "noise_passes_allowed": noise_passes_allowed,
        "noise_within_bar": (
            None if noise_passes_allowed is None else noise_passed <= noise_passes_allowed
        ),
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


def clopper_pearson_95(successes: int, trials: int) -> tuple[float | None, float | None]:
    """The exact (Clopper-Pearson) 95% range of a rate seen as ``successes`` of
    ``trials``: the rates under which a count this low, or this high, has at least a
    2.5% chance. Found by bisection on the binomial tail, so no statistics library."""
    if trials <= 0:
        return None, None

    def at_least(k: int, p: float) -> float:
        return sum(
            math.comb(trials, i) * p**i * (1 - p) ** (trials - i) for i in range(k, trials + 1)
        )

    def bisect(condition: Any) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if condition(mid) else (lo, mid)
        return (lo + hi) / 2

    low = 0.0 if successes == 0 else bisect(lambda p: at_least(successes, p) < 0.025)
    high = 1.0 if successes == trials else bisect(lambda p: 1 - at_least(successes + 1, p) >= 0.025)
    return low, high
