"""Tests for the strategy catalog and its synchronization with factory/config/CLI."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.strategy.catalog import (
    StrategyCatalogEntry,
    get_strategy_definition,
    list_debug_strategy_types,
    list_production_strategy_types,
    list_strategy_types,
    strategy_type_exists,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory

# ---------------------------------------------------------------------------
# Expected strategy types — update here when adding a new strategy
# ---------------------------------------------------------------------------

_EXPECTED_ALL = {
    "stub",
    "intentional_loser",
    "random",
    "moving_average_crossover",
    "momentum",
    "mean_reversion",
    "factor_based",
}

_EXPECTED_DEBUG = {"stub", "intentional_loser", "random"}
_EXPECTED_PRODUCTION = _EXPECTED_ALL - _EXPECTED_DEBUG


# ---------------------------------------------------------------------------
# Catalog content
# ---------------------------------------------------------------------------


def test_all_expected_strategies_are_cataloged() -> None:
    assert set(list_strategy_types()) == _EXPECTED_ALL


def test_debug_strategies_are_marked_debug() -> None:
    assert set(list_debug_strategy_types()) == _EXPECTED_DEBUG


def test_production_strategies_are_not_marked_debug() -> None:
    assert set(list_production_strategy_types()) == _EXPECTED_PRODUCTION


def test_debug_and_production_are_disjoint() -> None:
    assert not (set(list_debug_strategy_types()) & set(list_production_strategy_types()))


def test_debug_and_production_union_equals_all() -> None:
    all_types = set(list_strategy_types())
    combined = set(list_debug_strategy_types()) | set(list_production_strategy_types())
    assert combined == all_types


def test_strategy_type_exists_returns_true_for_known() -> None:
    for t in list_strategy_types():
        assert strategy_type_exists(t)


def test_strategy_type_exists_returns_false_for_unknown() -> None:
    assert not strategy_type_exists("not_a_real_strategy")


def test_get_strategy_definition_returns_entry() -> None:
    entry = get_strategy_definition("momentum")
    assert isinstance(entry, StrategyCatalogEntry)
    assert entry.strategy_type == "momentum"


def test_get_strategy_definition_raises_for_unknown() -> None:
    with pytest.raises(KeyError, match="Unknown strategy type"):
        get_strategy_definition("bogus_type")


def test_every_entry_has_non_empty_display_name_and_description() -> None:
    for t in list_strategy_types():
        entry = get_strategy_definition(t)
        assert entry.display_name.strip(), f"{t}: display_name is empty"
        assert entry.description.strip(), f"{t}: description is empty"


# ---------------------------------------------------------------------------
# StrategyFactory builds every cataloged strategy
# ---------------------------------------------------------------------------


factory = StrategyFactory()

_STRATEGY_IDS = {
    "stub": "stub-1",
    "intentional_loser": "loser-1",
    "random": "random-1",
    "moving_average_crossover": "mac-1",
    "momentum": "mom-1",
    "mean_reversion": "mr-1",
    "factor_based": "fb-1",
}


@pytest.mark.parametrize("strategy_type", list_strategy_types())
def test_factory_builds_every_cataloged_strategy(strategy_type: str) -> None:
    config = StrategyConfig(
        type=strategy_type,
        strategy_id=_STRATEGY_IDS[strategy_type],
    )
    strategy = factory.build(config)
    assert strategy is not None


# ---------------------------------------------------------------------------
# StrategyConfig rejects unknown types
# ---------------------------------------------------------------------------


def test_strategy_config_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError, match="Unknown strategy type"):
        StrategyConfig(type="totally_unknown", strategy_id="x")


@pytest.mark.parametrize("strategy_type", list_strategy_types())
def test_strategy_config_accepts_all_cataloged_types(strategy_type: str) -> None:
    cfg = StrategyConfig(type=strategy_type, strategy_id="test-id")
    assert cfg.type == strategy_type


# ---------------------------------------------------------------------------
# CLI choices synchronization
# ---------------------------------------------------------------------------


def test_cli_strategy_choices_match_catalog() -> None:
    from autonomous_trading_platform.cli.commands.research import STRATEGY_TYPE_CHOICES

    assert set(STRATEGY_TYPE_CHOICES) == set(list_strategy_types())
