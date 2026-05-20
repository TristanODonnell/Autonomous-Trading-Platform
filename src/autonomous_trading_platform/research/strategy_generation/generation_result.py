from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


@dataclass(frozen=True)
class GenerationOptions:
    seed: int = 42
    n_samples: int = 50
    population_size: int = 20
    generations: int = 3
    mutation_rate: float = 0.25
    include_debug: bool = False
    include_experimental: bool = False
    allowed_families: tuple[str, ...] = ()
    excluded_families: tuple[str, ...] = ()
    allowed_strategy_types: tuple[str, ...] = ()
    excluded_strategy_types: tuple[str, ...] = ()
    execution_mode: str | None = None
    price_basis: str | None = None


@dataclass
class GenerationSummary:
    generated_count: int = 0
    accepted_count: int = 0
    duplicate_count: int = 0
    rejected_count: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)
    strategy_type_distribution: Counter[str] = field(default_factory=Counter)
    family_distribution: Counter[str] = field(default_factory=Counter)
    rejected_details: list[dict[str, object]] = field(default_factory=list)
    duplicate_details: list[dict[str, object]] = field(default_factory=list)

    def reject(
        self,
        reason: str,
        *,
        strategy_type: str | None = None,
        parameters: dict | None = None,
        generator: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        self.rejected_count += 1
        self.rejection_reasons[reason] += 1
        self.rejected_details.append(
            {
                "reason": reason,
                "strategy_type": strategy_type,
                "parameters": parameters,
                "generator": generator,
                "config_hash": config_hash,
            }
        )

    def duplicate(
        self,
        *,
        strategy_type: str,
        config_hash: str,
        generator: str | None = None,
        parameters: dict | None = None,
    ) -> None:
        self.duplicate_count += 1
        self.duplicate_details.append(
            {
                "strategy_type": strategy_type,
                "config_hash": config_hash,
                "duplicate_hash": config_hash,
                "generator": generator,
                "parameters": parameters,
                "reason": "duplicate_config_hash",
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_count": self.generated_count,
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "rejected_count": self.rejected_count,
            "rejection_reasons": dict(sorted(self.rejection_reasons.items())),
            "strategy_type_distribution": dict(sorted(self.strategy_type_distribution.items())),
            "family_distribution": dict(sorted(self.family_distribution.items())),
            "rejected_details": self.rejected_details,
            "duplicate_details": self.duplicate_details,
        }


@dataclass
class GenerationResult(Sequence[StrategyConfig]):
    configs: list[StrategyConfig]
    summary: GenerationSummary

    def __iter__(self) -> Iterator[StrategyConfig]:
        return iter(self.configs)

    def __len__(self) -> int:
        return len(self.configs)

    def __getitem__(self, index):
        return self.configs[index]
