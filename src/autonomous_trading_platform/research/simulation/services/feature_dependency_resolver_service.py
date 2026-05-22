"""FeatureDependencyResolverService

Translates a strategy's declared persisted-feature dependencies into validated
SimulationFeatureDatasetRequest objects and a registry-derived warmup bar count.

This is the missing layer described in the feature dependency integration audit:
strategy dependency declarations → validated feature dataset requests → simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationFeatureDatasetRequest,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_feature_repository import (
    FEATURE_DATASETS_BY_NAME,
)
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
)
from autonomous_trading_platform.strategy.registry.strategy_registry import (
    StrategyRegistry,
    get_registry,
)


class FeatureDependencyError(Exception):
    """Raised when a required feature dataset cannot be resolved or fails lineage validation."""


@dataclass
class ResolvedFeatureDependencies:
    """Result returned by FeatureDependencyResolverService.resolve()."""

    feature_requests: list[SimulationFeatureDatasetRequest]
    resolved_feature_dataset_ids: dict[str, str] = field(default_factory=dict)
    warmup_bars: int = 0


class FeatureDependencyResolverService:
    """Resolves strategy feature dependencies for a simulation run.

    For each feature name declared in StrategyDefinition.required_persisted_features:
    - looks up the feature's ParquetDataset mapping
    - queries for a validated FeatureDatasetVersion that satisfies lineage,
      price basis, date coverage, and symbol coverage requirements
    - converts the match to a SimulationFeatureDatasetRequest

    Also computes registry-derived warmup_bars via
    StrategyDefinition.compute_warmup_bars() so the caller no longer needs
    parameter-name heuristics.

    Strategies with no required persisted features return an empty request list
    with registry warmup bars and are otherwise unchanged.
    """

    SUPPORTED_FEATURES: frozenset[str] = frozenset(FEATURE_DATASETS_BY_NAME)

    def __init__(
        self,
        feature_dataset_repository: FeatureDatasetVersionsRepository,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._repo = feature_dataset_repository
        self._registry = registry if registry is not None else get_registry()

    def resolve(
        self,
        *,
        strategy_type: str,
        strategy_parameters: dict[str, Any] | None,
        simulation_dataset_version: str,
        price_basis: PriceBasis,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> ResolvedFeatureDependencies:
        """Resolve all persisted feature dependencies for the given simulation request.

        Returns ResolvedFeatureDependencies with feature_requests populated for
        every required feature, plus registry-derived warmup_bars.

        Raises FeatureDependencyError immediately on any unresolvable dependency.
        """
        defn = self._registry.get_definition(strategy_type)
        normalized_params = defn.normalize_parameters(strategy_parameters)
        warmup_bars = defn.compute_warmup_bars(normalized_params)

        if not defn.required_persisted_features:
            return ResolvedFeatureDependencies(
                feature_requests=[],
                resolved_feature_dataset_ids={},
                warmup_bars=warmup_bars,
            )

        self._validate_feature_names(strategy_type, defn.required_persisted_features)

        feature_requests: list[SimulationFeatureDatasetRequest] = []
        resolved_ids: dict[str, str] = {}

        for feature_name in defn.required_persisted_features:
            parquet_dataset = FEATURE_DATASETS_BY_NAME[feature_name]
            min_symbol_count = len({s.upper() for s in symbols})

            row = self._repo.find_for_simulation(
                feature_name=feature_name,
                source_dataset_version=simulation_dataset_version,
                price_basis=price_basis,
                start_date=start_date,
                end_date=end_date,
                min_symbol_count=min_symbol_count,
            )

            if row is None:
                raise FeatureDependencyError(
                    f"No validated feature dataset found for "
                    f"feature={feature_name!r}, "
                    f"source_dataset_version={simulation_dataset_version!r}, "
                    f"price_basis={price_basis.value!r}, "
                    f"window={start_date}..{end_date}, "
                    f"min_symbols={min_symbol_count}. "
                    f"Ensure a feature pipeline job has run and validated a "
                    f"dataset for this bar dataset version and price basis."
                )

            feature_requests.append(
                SimulationFeatureDatasetRequest(
                    feature_name=feature_name,
                    dataset=parquet_dataset,
                    dataset_version=row.dataset_version_id,
                )
            )
            resolved_ids[feature_name] = row.dataset_version_id

        return ResolvedFeatureDependencies(
            feature_requests=feature_requests,
            resolved_feature_dataset_ids=resolved_ids,
            warmup_bars=warmup_bars,
        )

    def _validate_feature_names(
        self,
        strategy_type: str,
        required_features: tuple[str, ...],
    ) -> None:
        unknown = [f for f in required_features if f not in self.SUPPORTED_FEATURES]
        if unknown:
            raise FeatureDependencyError(
                f"Strategy {strategy_type!r} declares persisted feature(s) "
                f"{unknown} that have no Parquet dataset mapping. "
                f"Supported features: {sorted(self.SUPPORTED_FEATURES)}. "
                f"Do not add new feature mappings without a corresponding "
                f"persisted feature dataset job."
            )
