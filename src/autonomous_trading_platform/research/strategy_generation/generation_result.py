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

    def reject(self, reason: str) -> None:
        self.rejected_count += 1
        self.rejection_reasons[reason] += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_count": self.generated_count,
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "rejected_count": self.rejected_count,
            "rejection_reasons": dict(sorted(self.rejection_reasons.items())),
            "strategy_type_distribution": dict(sorted(self.strategy_type_distribution.items())),
            "family_distribution": dict(sorted(self.family_distribution.items())),
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
