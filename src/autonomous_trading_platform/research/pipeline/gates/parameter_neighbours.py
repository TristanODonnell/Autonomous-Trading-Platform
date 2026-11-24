"""Parameter neighbours: a strategy with one of its settings moved a little (plan R13).

A strategy that earns at a lookback of 20 bars and loses at 16 and at 24 has found a
coincidence in the bars, not a property of them. The check for it needs the strategy's
neighbours: the same strategy with one setting moved down, and with it moved up, every
other setting left alone. This module builds them. It runs nothing and judges nothing
(the stage and the gate do that).

The move
--------
The values research draws a setting from are no use as steps: a momentum lookback is
drawn from 10 or 60 bars, and 60 is another strategy, not a neighbour. So the move has a
rule of its own (``NudgeRule``):

- a whole number (every one of them today is a window or a lookback, in bars) moves by a
  share of its own value, and by one bar at least;
- a number with a declared range moves by a share of that range. A share of its own
  value would not do: a threshold of 0.0 has no share to move by;
- a number with no declared range (a composite's thresholds) moves by a share of its own
  value, and is skipped at 0.0, where there is no scale to go by.

Nothing is left out without saying so. A neighbour that cannot be built comes back with
the reason in ``skipped``: outside the setting's declared range, refused by the
strategy's own validation (a short window moved past the long one), or no scale.
The declared range is the registry's, the range a strategy may run at. The research
floor on windows is a rule about what to search and is not applied here: the neighbour
of a 10-bar lookback is 8 bars, and it is run.

What counts as a setting
------------------------
The four plain families declare theirs in the strategy registry: every tunable number
is one. A composite declares none: it is one nested template. Its settings are the
numbers that say how far back an indicator looks and where a rule or filter draws its
line (``_COMPOSITE_RULE_SETTINGS`` and the indicators' own parameters). The other
numbers in a template are how it is wired, and are listed in ``COMPOSITE_STRUCTURE``:
which bar an input reads, what a rule's vote weighs, and the aggregator's thresholds.
Those last are not moved because they are set against the rules' fixed confidences
(0.55 under a confidence of 0.58): moved up by a fifth, the aggregator can no longer
be reached and the strategy never trades, which says nothing about the strategy.

A neighbour is a strategy in its own right, with its own content-hash id from
``make_config``. It never carries the id of the strategy it was made from: the
simulation runner stores a run's config under the run's strategy id.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeGuard

from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import (
    ParameterSpec,
    ParameterType,
    get_registry,
)

DOWN = "down"
UP = "up"
DIRECTIONS: tuple[str, str] = (DOWN, UP)

COMPOSITE_TYPE = "composite_rule"

# The numbers of a composite's rules and filters that are settings: where the line is.
_COMPOSITE_RULE_SETTINGS = ("buy_above", "sell_below", "buy_below", "sell_above")
_COMPOSITE_FILTER_SETTINGS = ("threshold",)

# The numbers of a composite template that are wiring and are not moved, as paths with
# the list positions left out. tests/.../test_parameter_neighbours.py holds every
# number of every template in the generator's pool to be a setting or one of these.
COMPOSITE_STRUCTURE: frozenset[str] = frozenset(
    {
        "entry_rules.inputs.offset",
        "entry_rules.parameters.confidence",
        "entry_rules.weight",
        "confirmations.inputs.offset",
        "confirmations.parameters.confidence",
        "confirmations.weight",
        "filters.input.offset",
        "aggregator.parameters.min_votes",
        "aggregator.parameters.buy_threshold",
        "aggregator.parameters.sell_threshold",
        "confidence_scoring.floor",
        "confidence_scoring.cap",
    }
)


@dataclass(frozen=True)
class NudgeRule:
    """How far a setting is moved to make a neighbour. Each is a share, in (0, 1)."""

    # Whole numbers (windows, lookbacks): share of the value, one bar at least.
    window_fraction: float = 0.20
    # Numbers with a declared range: share of the range.
    range_fraction: float = 0.10
    # Numbers with no declared range: share of the value.
    unranged_fraction: float = 0.20

    def __post_init__(self) -> None:
        for name in ("window_fraction", "range_fraction", "unranged_fraction"):
            value = getattr(self, name)
            if not (0.0 < value < 1.0):
                raise ValueError(f"{name} must be in (0, 1), got {value}")


@dataclass(frozen=True)
class Neighbour:
    """One setting moved one way. ``config`` is the strategy to run; when the neighbour
    could not be built it is None and ``skipped`` says why."""

    setting: str
    direction: str
    base_value: float
    value: float | None = None
    config: StrategyConfig | None = None
    skipped: str | None = None


@dataclass(frozen=True)
class _Setting:
    """A number in a strategy's parameters: where it is and how it may move."""

    label: str
    path: tuple[str | int, ...]
    value: float
    whole: bool
    min_value: float | None = None
    max_value: float | None = None


def build_neighbours(config: StrategyConfig, rule: NudgeRule | None = None) -> list[Neighbour]:
    """Every setting of ``config`` moved down and moved up: two entries per setting, in
    the settings' own order, each a strategy to run or a reason it was not built."""
    rule = rule or NudgeRule()
    neighbours: list[Neighbour] = []
    for setting in _settings(config):
        step = _step(setting, rule)
        for direction in DIRECTIONS:
            neighbours.append(_neighbour(config, setting, direction, step))
    return neighbours


def composite_numbers(parameters: dict[str, Any]) -> Iterator[tuple[str, bool]]:
    """Every number in a composite template, as (its path without list positions,
    whether it is a setting). For the test that no number goes unaccounted for."""
    settings = {setting.path for setting in _composite_settings(parameters)}
    for path, _value in _numbers(parameters, ()):
        if path[0] in ("sizing", "metadata"):
            continue
        yield _generic(path), path in settings


# ---------------------------------------------------------------------------
# Which numbers are settings
# ---------------------------------------------------------------------------


def _settings(config: StrategyConfig) -> list[_Setting]:
    if config.type == COMPOSITE_TYPE:
        return _composite_settings(config.parameters)
    specs: tuple[ParameterSpec, ...] = tuple(
        get_registry().get_definition(config.type).parameter_specs
    )
    settings: list[_Setting] = []
    for spec in specs:
        if not spec.tunable or spec.parameter_type not in (ParameterType.INT, ParameterType.FLOAT):
            continue
        value = config.parameters.get(spec.name)
        if not _is_number(value):
            continue
        settings.append(
            _Setting(
                label=spec.name,
                path=(spec.name,),
                value=float(value),
                whole=spec.parameter_type == ParameterType.INT,
                min_value=spec.min_value,
                max_value=spec.max_value,
            )
        )
    return settings


def _composite_settings(parameters: dict[str, Any]) -> list[_Setting]:
    settings: list[_Setting] = []
    for index, indicator in enumerate(parameters.get("indicators") or []):
        for name, value in (indicator.get("parameters") or {}).items():
            if _is_number(value):
                settings.append(
                    _Setting(
                        label=f"indicators.{indicator.get('id', index)}.{name}",
                        path=("indicators", index, "parameters", name),
                        value=float(value),
                        whole=isinstance(value, int),
                    )
                )
    for section in ("entry_rules", "confirmations"):
        for index, rule in enumerate(parameters.get(section) or []):
            for name in _COMPOSITE_RULE_SETTINGS:
                value = (rule.get("parameters") or {}).get(name)
                if _is_number(value):
                    settings.append(
                        _Setting(
                            label=f"{section}.{rule.get('id', index)}.{name}",
                            path=(section, index, "parameters", name),
                            value=float(value),
                            whole=False,
                        )
                    )
    for index, filter_cfg in enumerate(parameters.get("filters") or []):
        for name in _COMPOSITE_FILTER_SETTINGS:
            value = filter_cfg.get(name)
            if _is_number(value):
                settings.append(
                    _Setting(
                        label=f"filters.{filter_cfg.get('id', index)}.{name}",
                        path=("filters", index, name),
                        value=float(value),
                        whole=False,
                    )
                )
    return settings


# ---------------------------------------------------------------------------
# Moving one
# ---------------------------------------------------------------------------


def _step(setting: _Setting, rule: NudgeRule) -> float | None:
    """How far the setting moves; None when there is no scale to move it by."""
    if setting.whole:
        return float(max(1, round(abs(setting.value) * rule.window_fraction)))
    if setting.min_value is not None and setting.max_value is not None:
        return (setting.max_value - setting.min_value) * rule.range_fraction
    if setting.value == 0.0:
        return None
    return abs(setting.value) * rule.unranged_fraction


def _neighbour(
    config: StrategyConfig, setting: _Setting, direction: str, step: float | None
) -> Neighbour:
    def skipped(reason: str, value: float | None = None) -> Neighbour:
        return Neighbour(
            setting=setting.label,
            direction=direction,
            base_value=setting.value,
            value=value,
            skipped=reason,
        )

    if step is None or step <= 0.0:
        return skipped("a value of zero with no declared range: no scale to move it by")
    moved = setting.value - step if direction == DOWN else setting.value + step
    moved = float(round(moved)) if setting.whole else round(moved, 10)
    if setting.min_value is not None and moved < setting.min_value:
        return skipped(f"below the declared minimum {setting.min_value:g}", moved)
    if setting.max_value is not None and moved > setting.max_value:
        return skipped(f"above the declared maximum {setting.max_value:g}", moved)

    parameters = deepcopy(config.parameters)
    target: Any = parameters
    for key in setting.path[:-1]:
        target = target[key]
    target[setting.path[-1]] = int(moved) if setting.whole else moved
    try:
        neighbour = make_config(config.type, parameters)
    except ValueError as exc:
        return skipped(f"not a valid strategy: {_one_line(exc)}", moved)
    if neighbour.strategy_id == config.strategy_id:
        return skipped("the move left the strategy unchanged", moved)
    return Neighbour(
        setting=setting.label,
        direction=direction,
        base_value=setting.value,
        value=moved,
        config=neighbour,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers(node: Any, path: tuple[str | int, ...]) -> Iterator[tuple[tuple[str | int, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _numbers(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _numbers(value, (*path, index))
    elif _is_number(node):
        yield path, node


def _generic(path: tuple[str | int, ...]) -> str:
    """A path with the list positions and the names of a rule's inputs left out:
    ``entry_rules[0].inputs.current_fast.offset`` is ``entry_rules.inputs.offset``."""
    parts = [str(part) for part in path if not isinstance(part, int)]
    if "inputs" in parts:
        at = parts.index("inputs")
        parts = [*parts[: at + 1], *parts[at + 2 :]]
    return ".".join(parts)


def _one_line(exc: Exception) -> str:
    """Why the strategy's own validation refused it, without the input it echoes back."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        text = "; ".join(str(error.get("msg", "")) for error in errors())
        text = text.replace("Value error, ", "")
    else:
        text = str(exc)
    text = " ".join(text.split())
    return text if len(text) <= 200 else text[:197] + "..."
