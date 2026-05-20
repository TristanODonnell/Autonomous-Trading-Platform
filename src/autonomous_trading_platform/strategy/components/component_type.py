"""Component type taxonomy for reusable strategy-construction primitives."""

from __future__ import annotations

from enum import StrEnum


class ComponentType(StrEnum):
    INDICATOR = "indicator"
    SIGNAL_RULE = "signal_rule"
    FILTER = "filter"
    AGGREGATOR = "aggregator"
    EXIT_RULE = "exit_rule"
    SIZING = "sizing"
