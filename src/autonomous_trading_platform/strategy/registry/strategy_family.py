"""Canonical strategy family classifications."""

from __future__ import annotations

from enum import StrEnum


class StrategyFamily(StrEnum):
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    TREND = "trend"
    FACTOR = "factor"
    COMPOSITE = "composite"
    ENSEMBLE = "ensemble"
    DEBUG = "debug"
