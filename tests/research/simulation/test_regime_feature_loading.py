"""
Tests that regime_classification integrates correctly with the TASK-2.1
dependency resolution and StrategyContext feature loading pipeline.

These tests use the same mocked-resolver pattern established in
test_feature_dependency_resolver_service.py — no real DB or Parquet I/O.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.services.feature_dependency_resolver_service import (
    FeatureDependencyError,
    FeatureDependencyResolverService,
    ResolvedFeatureDependencies,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationFeatureDatasetRequest,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_feature_repository import (
    FEATURE_DATASETS_BY_NAME,
)
from autonomous_trading_platform.strategy.registry.strategy_definition import StrategyDefinition
from autonomous_trading_platform.strategy.registry.strategy_family import StrategyFamily
from autonomous_trading_platform.strategy.registry.strategy_registry import StrategyRegistry

_SYMBOLS = ["AAPL", "TSLA"]
_START = date(2024, 1, 2)
_END = date(2024, 3, 29)
_DATASET_VERSION = "bars-v1"
_PRICE_BASIS = PriceBasis.RAW


def _no_op_validator(p: dict[str, Any]) -> None:
    pass


def _make_registry_with_regime_classification() -> StrategyRegistry:
    from autonomous_trading_platform.strategy.registry.parameter_schemas import (
        StrategyParameterSchema,
    )

    registry = StrategyRegistry()
    registry.register(
        StrategyDefinition(
            strategy_type="test_regime_classification",
            display_name="Test Regime Classification",
            description="Strategy that requires regime_classification persisted feature.",
            family=StrategyFamily.DEBUG,
            implementation_class=object,
            debug=True,
            production_ready=False,
            default_parameters={},
            parameter_validator=_no_op_validator,
            parameter_schema=StrategyParameterSchema,
            warmup_bars_fn=lambda p: 200,
            required_indicators=(),
            required_persisted_features=("regime_classification",),
            parameter_specs=(),
            builder=lambda sid, p: None,
            deterministic=True,
        )
    )
    registry.lock()
    return registry


def _make_regime_feature_row(
    dataset_version_id: str = "regime-class-001",
    price_basis: PriceBasis = _PRICE_BASIS,
    source_dataset_version: str = _DATASET_VERSION,
    symbol_coverage: int = 5,
    date_coverage_start: date = date(2023, 1, 1),
    date_coverage_end: date = date(2024, 12, 31),
) -> MagicMock:
    row = MagicMock()
    row.dataset_version_id = dataset_version_id
    row.underlying_price_basis = price_basis
    row.source_dataset_version = source_dataset_version
    row.symbol_coverage = symbol_coverage
    row.date_coverage_start = date_coverage_start
    row.date_coverage_end = date_coverage_end
    row.feature_name = "regime_classification"
    row.validation_status = "validated"
    row.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    return row


# ---------------------------------------------------------------------------
# Tests: FEATURE_DATASETS_BY_NAME registration
# ---------------------------------------------------------------------------


def test_regime_classification_registered_in_feature_datasets_by_name() -> None:
    assert "regime_classification" in FEATURE_DATASETS_BY_NAME


def test_regime_classification_dataset_uses_correct_key() -> None:
    dataset = FEATURE_DATASETS_BY_NAME["regime_classification"]
    assert dataset.dataset_key == "feature_regime_classification"


def test_regime_classification_dataset_has_required_schema_columns() -> None:
    dataset = FEATURE_DATASETS_BY_NAME["regime_classification"]
    schema_names = dataset.schema.names
    for col in [
        "regime_trend",
        "regime_volatility",
        "regime_liquidity",
        "regime_mean_reversion",
        "regime_risk",
        "underlying_dataset_version",
        "price_basis",
        "year",
        "month",
    ]:
        assert col in schema_names, f"Missing schema column: {col}"


# ---------------------------------------------------------------------------
# Tests: resolver resolves regime_classification
# ---------------------------------------------------------------------------


def test_resolver_resolves_regime_classification_feature() -> None:
    registry = _make_registry_with_regime_classification()
    mock_repo = MagicMock()
    mock_repo.find_for_simulation.return_value = _make_regime_feature_row()

    resolver = FeatureDependencyResolverService(
        feature_dataset_repository=mock_repo,
        registry=registry,
    )
    result = resolver.resolve(
        strategy_type="test_regime_classification",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert isinstance(result, ResolvedFeatureDependencies)
    assert len(result.feature_requests) == 1
    req = result.feature_requests[0]
    assert isinstance(req, SimulationFeatureDatasetRequest)
    assert req.feature_name == "regime_classification"
    assert req.dataset_version == "regime-class-001"
    assert "regime_classification" in result.resolved_feature_dataset_ids


def test_resolver_raises_when_no_validated_regime_classification() -> None:
    registry = _make_registry_with_regime_classification()
    mock_repo = MagicMock()
    mock_repo.find_for_simulation.return_value = None

    resolver = FeatureDependencyResolverService(
        feature_dataset_repository=mock_repo,
        registry=registry,
    )
    with pytest.raises(FeatureDependencyError, match="regime_classification"):
        resolver.resolve(
            strategy_type="test_regime_classification",
            strategy_parameters={},
            simulation_dataset_version=_DATASET_VERSION,
            price_basis=_PRICE_BASIS,
            symbols=_SYMBOLS,
            start_date=_START,
            end_date=_END,
        )


def test_resolved_feature_dataset_ids_include_regime_classification() -> None:
    registry = _make_registry_with_regime_classification()
    mock_repo = MagicMock()
    row = _make_regime_feature_row(dataset_version_id="rc-v42")
    mock_repo.find_for_simulation.return_value = row

    resolver = FeatureDependencyResolverService(
        feature_dataset_repository=mock_repo,
        registry=registry,
    )
    result = resolver.resolve(
        strategy_type="test_regime_classification",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert result.resolved_feature_dataset_ids["regime_classification"] == "rc-v42"


# ---------------------------------------------------------------------------
# Tests: regime-classification dataset separate from legacy regime dataset
# ---------------------------------------------------------------------------


def test_legacy_regime_and_regime_classification_are_distinct_datasets() -> None:
    assert "regime" in FEATURE_DATASETS_BY_NAME
    assert "regime_classification" in FEATURE_DATASETS_BY_NAME
    assert (
        FEATURE_DATASETS_BY_NAME["regime"].dataset_key
        != FEATURE_DATASETS_BY_NAME["regime_classification"].dataset_key
    )
    assert (
        FEATURE_DATASETS_BY_NAME["regime"].root_parts
        != FEATURE_DATASETS_BY_NAME["regime_classification"].root_parts
    )
