"""Unit tests for FeatureDependencyResolverService.

Tests the resolver in isolation using a synthetic test registry entry and a
mocked FeatureDatasetVersionsRepository. No real database or Parquet I/O.
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
from autonomous_trading_platform.storage.parquet.datasets import FEATURE_RETURNS_DATASET
from autonomous_trading_platform.strategy.registry.strategy_definition import StrategyDefinition
from autonomous_trading_platform.strategy.registry.strategy_family import StrategyFamily
from autonomous_trading_platform.strategy.registry.strategy_registry import StrategyRegistry

# ---------------------------------------------------------------------------
# Test registry with one strategy that declares no persisted features and one
# that declares "returns".
# ---------------------------------------------------------------------------

_SYMBOLS = ["AAPL", "TSLA"]
_START = date(2024, 1, 2)
_END = date(2024, 3, 29)
_DATASET_VERSION = "bars-v1"
_PRICE_BASIS = PriceBasis.RAW


def _no_op_validator(p: dict[str, Any]) -> None:
    pass


def _make_test_registry(*, with_feature_strategy: bool = False) -> StrategyRegistry:
    """Return a fresh StrategyRegistry with synthetic test strategy definitions."""
    from autonomous_trading_platform.strategy.registry.parameter_schemas import (
        StrategyParameterSchema,
    )

    registry = StrategyRegistry()

    registry.register(
        StrategyDefinition(
            strategy_type="test_no_features",
            display_name="Test No Features",
            description="Test strategy with no persisted features.",
            family=StrategyFamily.DEBUG,
            implementation_class=object,
            debug=True,
            production_ready=False,
            default_parameters={},
            parameter_validator=_no_op_validator,
            parameter_schema=StrategyParameterSchema,
            warmup_bars_fn=lambda p: 5,
            required_indicators=(),
            required_persisted_features=(),
            parameter_specs=(),
            builder=lambda sid, p: None,
            deterministic=True,
        )
    )

    if with_feature_strategy:
        registry.register(
            StrategyDefinition(
                strategy_type="test_with_returns",
                display_name="Test With Returns",
                description="Test strategy that requires the returns feature.",
                family=StrategyFamily.MOMENTUM,
                implementation_class=object,
                debug=False,
                production_ready=False,
                default_parameters={},
                parameter_validator=_no_op_validator,
                parameter_schema=StrategyParameterSchema,
                warmup_bars_fn=lambda p: 10,
                required_indicators=(),
                required_persisted_features=("returns",),
                parameter_specs=(),
                builder=lambda sid, p: None,
                deterministic=True,
            )
        )

    registry.lock()
    return registry


def _make_feature_row(
    dataset_version_id: str = "feat-returns-001",
    price_basis: PriceBasis = _PRICE_BASIS,
    source_dataset_version: str = _DATASET_VERSION,
    symbol_coverage: int = 5,
    date_coverage_start: date = date(2023, 1, 1),
    date_coverage_end: date = date(2024, 12, 31),
):
    """Build a minimal mock FeatureDatasetVersions row."""
    row = MagicMock()
    row.dataset_version_id = dataset_version_id
    row.underlying_price_basis = price_basis
    row.source_dataset_version = source_dataset_version
    row.symbol_coverage = symbol_coverage
    row.date_coverage_start = date_coverage_start
    row.date_coverage_end = date_coverage_end
    row.feature_name = "returns"
    row.validation_status = "validated"
    row.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    return row


def _make_resolver(
    *,
    repo_row=None,
    with_feature_strategy: bool = False,
) -> tuple[FeatureDependencyResolverService, MagicMock]:
    registry = _make_test_registry(with_feature_strategy=with_feature_strategy)
    mock_repo = MagicMock()
    mock_repo.find_for_simulation.return_value = repo_row
    resolver = FeatureDependencyResolverService(
        feature_dataset_repository=mock_repo,
        registry=registry,
    )
    return resolver, mock_repo


# ---------------------------------------------------------------------------
# Tests: strategies with no persisted features
# ---------------------------------------------------------------------------


def test_resolve_returns_empty_requests_when_no_persisted_features() -> None:
    resolver, mock_repo = _make_resolver(with_feature_strategy=False)

    result = resolver.resolve(
        strategy_type="test_no_features",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert isinstance(result, ResolvedFeatureDependencies)
    assert result.feature_requests == []
    assert result.resolved_feature_dataset_ids == {}
    mock_repo.find_for_simulation.assert_not_called()


def test_resolve_returns_warmup_bars_for_no_feature_strategy() -> None:
    resolver, _ = _make_resolver(with_feature_strategy=False)

    result = resolver.resolve(
        strategy_type="test_no_features",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert result.warmup_bars == 5


# ---------------------------------------------------------------------------
# Tests: successful resolution
# ---------------------------------------------------------------------------


def test_resolve_returns_feature_request_for_valid_dataset() -> None:
    feature_row = _make_feature_row()
    resolver, mock_repo = _make_resolver(repo_row=feature_row, with_feature_strategy=True)

    result = resolver.resolve(
        strategy_type="test_with_returns",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert len(result.feature_requests) == 1
    req: SimulationFeatureDatasetRequest = result.feature_requests[0]
    assert req.feature_name == "returns"
    assert req.dataset == FEATURE_RETURNS_DATASET
    assert req.dataset_version == "feat-returns-001"


def test_resolve_records_dataset_id_in_metadata() -> None:
    feature_row = _make_feature_row(dataset_version_id="feat-returns-abc")
    resolver, _ = _make_resolver(repo_row=feature_row, with_feature_strategy=True)

    result = resolver.resolve(
        strategy_type="test_with_returns",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert result.resolved_feature_dataset_ids == {"returns": "feat-returns-abc"}


def test_resolve_returns_warmup_bars_for_feature_strategy() -> None:
    feature_row = _make_feature_row()
    resolver, _ = _make_resolver(repo_row=feature_row, with_feature_strategy=True)

    result = resolver.resolve(
        strategy_type="test_with_returns",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    assert result.warmup_bars == 10


def test_resolve_calls_repo_with_correct_arguments() -> None:
    feature_row = _make_feature_row()
    resolver, mock_repo = _make_resolver(repo_row=feature_row, with_feature_strategy=True)

    resolver.resolve(
        strategy_type="test_with_returns",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    mock_repo.find_for_simulation.assert_called_once_with(
        feature_name="returns",
        source_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        start_date=_START,
        end_date=_END,
        min_symbol_count=len(_SYMBOLS),
    )


# ---------------------------------------------------------------------------
# Tests: missing dataset
# ---------------------------------------------------------------------------


def test_resolve_raises_when_feature_dataset_not_found() -> None:
    resolver, _ = _make_resolver(repo_row=None, with_feature_strategy=True)

    with pytest.raises(FeatureDependencyError, match="No validated feature dataset found"):
        resolver.resolve(
            strategy_type="test_with_returns",
            strategy_parameters={},
            simulation_dataset_version=_DATASET_VERSION,
            price_basis=_PRICE_BASIS,
            symbols=_SYMBOLS,
            start_date=_START,
            end_date=_END,
        )


def test_resolve_error_message_includes_feature_name_and_version() -> None:
    resolver, _ = _make_resolver(repo_row=None, with_feature_strategy=True)

    with pytest.raises(FeatureDependencyError) as exc_info:
        resolver.resolve(
            strategy_type="test_with_returns",
            strategy_parameters={},
            simulation_dataset_version="bars-missing-v99",
            price_basis=_PRICE_BASIS,
            symbols=_SYMBOLS,
            start_date=_START,
            end_date=_END,
        )

    msg = str(exc_info.value)
    assert "returns" in msg
    assert "bars-missing-v99" in msg


def test_resolve_error_message_includes_price_basis() -> None:
    resolver, _ = _make_resolver(repo_row=None, with_feature_strategy=True)

    with pytest.raises(FeatureDependencyError) as exc_info:
        resolver.resolve(
            strategy_type="test_with_returns",
            strategy_parameters={},
            simulation_dataset_version=_DATASET_VERSION,
            price_basis=PriceBasis.ADJUSTED,
            symbols=_SYMBOLS,
            start_date=_START,
            end_date=_END,
        )

    assert "adjusted" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: unknown feature name in strategy definition
# ---------------------------------------------------------------------------


def test_resolve_raises_for_unsupported_feature_name() -> None:
    """A strategy declaring a feature not in FEATURE_DATASETS_BY_NAME must fail."""
    from autonomous_trading_platform.strategy.registry.parameter_schemas import (
        StrategyParameterSchema,
    )

    registry = StrategyRegistry()
    registry.register(
        StrategyDefinition(
            strategy_type="test_bad_feature",
            display_name="Test Bad Feature",
            description="Declares an unknown feature.",
            family=StrategyFamily.DEBUG,
            implementation_class=object,
            debug=True,
            production_ready=False,
            default_parameters={},
            parameter_validator=_no_op_validator,
            parameter_schema=StrategyParameterSchema,
            warmup_bars_fn=lambda p: 0,
            required_indicators=(),
            required_persisted_features=("nonexistent_feature",),
            parameter_specs=(),
            builder=lambda sid, p: None,
            deterministic=True,
        )
    )
    registry.lock()

    mock_repo = MagicMock()
    resolver = FeatureDependencyResolverService(
        feature_dataset_repository=mock_repo,
        registry=registry,
    )

    with pytest.raises(FeatureDependencyError, match="nonexistent_feature"):
        resolver.resolve(
            strategy_type="test_bad_feature",
            strategy_parameters={},
            simulation_dataset_version=_DATASET_VERSION,
            price_basis=_PRICE_BASIS,
            symbols=_SYMBOLS,
            start_date=_START,
            end_date=_END,
        )

    mock_repo.find_for_simulation.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: determinism
# ---------------------------------------------------------------------------


def test_resolve_is_deterministic_for_same_inputs() -> None:
    feature_row = _make_feature_row()
    resolver, _ = _make_resolver(repo_row=feature_row, with_feature_strategy=True)

    kwargs: dict[str, Any] = dict(
        strategy_type="test_with_returns",
        strategy_parameters={},
        simulation_dataset_version=_DATASET_VERSION,
        price_basis=_PRICE_BASIS,
        symbols=_SYMBOLS,
        start_date=_START,
        end_date=_END,
    )

    result1 = resolver.resolve(**kwargs)
    result2 = resolver.resolve(**kwargs)

    assert result1.resolved_feature_dataset_ids == result2.resolved_feature_dataset_ids
    assert result1.warmup_bars == result2.warmup_bars
    assert len(result1.feature_requests) == len(result2.feature_requests)
