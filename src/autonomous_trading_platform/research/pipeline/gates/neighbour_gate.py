"""
NeighbourGate — pure rules for the parameter-neighbour stage (plan R13).

The evidence is the strategy re-run with one setting moved a little, once down and once
up, for each of its settings (``parameter_neighbours.py`` builds them), beside the
strategy's own run over the same days.

The question is whether the strategy's result is a property of the region its settings
sit in or of the exact numbers. So a neighbour is judged on the level of its result
against the strategy's own, not on how much the results vary: neighbours that are all
equally bad vary very little.

A neighbour **holds** when it traded and its Sharpe is at least
``min_share_of_own_sharpe`` of the strategy's own and at least ``min_neighbour_sharpe``.
A setting is **fragile** when none of its neighbours that were run holds: move it either
way and the result is gone. Each fragile setting is a finding.

Report only by default (``enforce=False``): the findings are recorded and the strategy
passes. The two thresholds have not been measured against anything; they say what the
report calls fragile, and every neighbour's numbers are on the verdict whatever they say.

What the gate cannot see: a neighbour that could not be built is not evidence either
way (``skipped`` carries each with its reason), and a setting with every neighbour
skipped is not checked. A strategy with no neighbour run at all passes with a warning.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class NeighbourGateConfig:
    min_share_of_own_sharpe: float = 0.5
    min_neighbour_sharpe: float = 0.0
    # False = report only: findings are recorded, the strategy passes.
    enforce: bool = False

    def __post_init__(self) -> None:
        if not (0.0 <= self.min_share_of_own_sharpe <= 1.0):
            raise ValueError(
                f"min_share_of_own_sharpe must be in [0, 1], got {self.min_share_of_own_sharpe}"
            )


@dataclass(frozen=True)
class NeighbourResult:
    """One neighbour's run. ``holds`` is the gate's reading of it (None = not judged)."""

    setting: str
    direction: str
    base_value: float
    value: float
    strategy_id: str
    sharpe: float
    max_drawdown: float
    total_return: float
    trade_count: int
    holds: bool | None = None


@dataclass(frozen=True)
class SkippedNeighbour:
    """A neighbour that was not run, and why."""

    setting: str
    direction: str
    base_value: float
    value: float | None
    reason: str


@dataclass(frozen=True)
class SettingCheck:
    setting: str
    base_value: float
    neighbours_run: int
    neighbours_holding: int

    @property
    def fragile(self) -> bool:
        return self.neighbours_run > 0 and self.neighbours_holding == 0


@dataclass(frozen=True)
class NeighbourGateVerdict:
    """Gate decision.

    ``findings`` is what the rule found, enforced or not. ``failure_reasons`` is the
    subset that eliminated the strategy: equal to ``findings`` when ``enforced``, empty
    otherwise (``passed`` is then always True).
    """

    strategy_id: str
    passed: bool
    own_sharpe: float | None
    # The Sharpe a neighbour needs to hold; None when the neighbours were not judged.
    sharpe_needed: float | None
    results: tuple[NeighbourResult, ...]
    skipped: tuple[SkippedNeighbour, ...]
    settings: tuple[SettingCheck, ...]
    holding_fraction: float | None
    median_neighbour_sharpe: float | None
    worst_neighbour_sharpe: float | None
    failure_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    findings: tuple[str, ...] = ()
    enforced: bool = False


def evaluate_neighbour_gate(
    *,
    strategy_id: str,
    own_sharpe: float | None,
    results: list[NeighbourResult],
    skipped: list[SkippedNeighbour],
    missing: int = 0,
    config: NeighbourGateConfig,
) -> NeighbourGateVerdict:
    """Judge a strategy on its neighbours.

    ``own_sharpe`` is the strategy's own run over the neighbours' days; None when
    there is none, and the neighbours are then recorded without being judged.
    ``missing`` counts neighbours that were asked for and gave no result.
    """
    findings: list[str] = []
    warnings: list[str] = []

    if missing:
        findings.append(f"no result for {missing} neighbour run(s) that were asked for")

    sharpe_needed: float | None = None
    judged: list[NeighbourResult] = list(results)
    if not results:
        warnings.append(
            "no neighbour was run: nothing checked"
            + (f" ({len(skipped)} could not be built)" if skipped else "")
        )
    elif own_sharpe is None:
        warnings.append(
            "no run of the strategy itself over these days: neighbours recorded, not judged"
        )
    else:
        sharpe_needed = max(
            config.min_neighbour_sharpe, own_sharpe * config.min_share_of_own_sharpe
        )
        judged = [
            replace(result, holds=result.trade_count > 0 and result.sharpe >= sharpe_needed)
            for result in results
        ]

    settings = _settings(judged, skipped)
    if sharpe_needed is not None:
        for check in settings:
            if check.fragile:
                findings.append(_describe_fragile(check, judged, own_sharpe, sharpe_needed))

    sharpes = [result.sharpe for result in judged if result.sharpe == result.sharpe]
    holding = [result for result in judged if result.holds]
    return NeighbourGateVerdict(
        strategy_id=strategy_id,
        passed=not findings if config.enforce else True,
        own_sharpe=own_sharpe,
        sharpe_needed=sharpe_needed,
        results=tuple(judged),
        skipped=tuple(skipped),
        settings=tuple(settings),
        holding_fraction=(
            len(holding) / len(judged) if judged and sharpe_needed is not None else None
        ),
        median_neighbour_sharpe=statistics.median(sharpes) if sharpes else None,
        worst_neighbour_sharpe=min(sharpes) if sharpes else None,
        failure_reasons=tuple(findings) if config.enforce else (),
        warnings=tuple(warnings),
        findings=tuple(findings),
        enforced=config.enforce,
    )


def _settings(
    results: list[NeighbourResult], skipped: list[SkippedNeighbour]
) -> list[SettingCheck]:
    """One check per setting: those with a neighbour run first, in the order they
    were run, then those whose neighbours were all skipped."""
    base_values: dict[str, float] = {}
    for result in results:
        base_values.setdefault(result.setting, result.base_value)
    for entry in skipped:
        base_values.setdefault(entry.setting, entry.base_value)
    checks: list[SettingCheck] = []
    for setting, base_value in base_values.items():
        run = [result for result in results if result.setting == setting]
        checks.append(
            SettingCheck(
                setting=setting,
                base_value=base_value,
                neighbours_run=len(run),
                neighbours_holding=sum(1 for result in run if result.holds),
            )
        )
    return checks


def _describe_fragile(
    check: SettingCheck,
    results: list[NeighbourResult],
    own_sharpe: float | None,
    sharpe_needed: float,
) -> str:
    seen = "; ".join(
        f"at {result.value:g}: "
        + ("no trades" if result.trade_count <= 0 else f"Sharpe {result.sharpe:.2f}")
        for result in results
        if result.setting == check.setting
    )
    own = "n/a" if own_sharpe is None else f"{own_sharpe:.2f}"
    which = (
        "neither neighbour holds"
        if check.neighbours_run == 2
        else "its one neighbour run does not hold"
    )
    return (
        f"{check.setting} = {check.base_value:g}: {which} ({seen}; "
        f"own Sharpe {own}, a neighbour needs {sharpe_needed:.2f})"
    )
