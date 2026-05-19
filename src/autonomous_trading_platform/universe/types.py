from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class UniverseResolutionMode(StrEnum):
    """Determines how an experiment resolves its universe scope."""

    FIXED_UNIVERSE_VERSION = "fixed_universe_version"
    ACTIVE_AS_OF_START_DATE = "active_universe_as_of_start_date"
    HISTORICAL_ROTATION_REPLAY = "historical_rotation_replay"
    CUSTOM = "explicit_custom_universe_version"


@dataclass
class UniverseTransition:
    """A single universe rotation event during a historical replay window."""

    effective_from: datetime
    effective_to: datetime | None
    universe_version_id: str
    added_symbols: list[str]
    removed_symbols: list[str]
    retained_symbols: list[str]


@dataclass
class ExperimentUniverseScope:
    """Fully resolved, PIT-anchored universe scope for an experiment or simulation."""

    mode: UniverseResolutionMode
    universe_version_id: str | None
    effective_timestamp: datetime | None
    member_symbols: list[str]
    replay_rotation_enabled: bool
    replay_transitions: list[UniverseTransition]
    source: str | None
    member_count: int

    @property
    def is_empty(self) -> bool:
        return self.member_count == 0 and not self.replay_rotation_enabled


@dataclass(frozen=True)
class UniverseAsset:
    symbol: str
    tradable: bool
    status: str
    asset_class: str


@dataclass
class UniverseValidationResult:
    """
    Result of validating a universe snapshot at the service layer.
    """

    ok: bool
    errors: list[str]

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False


class UniverseAssetSource(Protocol):
    def list_assets(self) -> list[UniverseAsset]: ...


@dataclass(frozen=True)
class RawSymbolRecord:
    symbol: str
    asset_type: str
    status: str
    is_tradable: bool
    exchange: str | None = None
    name: str | None = None
    currency: str | None = None
    country: str | None = None
    marginable: bool | None = None
    shortable: bool | None = None
    fractionable: bool | None = None
    easy_to_borrow: bool | None = None
    provider_symbol: str | None = None
    provider_asset_id: str | None = None


class RawSymbolProvider(Protocol):
    @property
    def source_name(self) -> str: ...

    def fetch_symbols(self) -> list[RawSymbolRecord]: ...


@dataclass(frozen=True)
class CandidateGenerationConfig:
    as_of: datetime
    lookback_days: int = 20
    min_history_days: int = 10
    min_price: Decimal = Decimal("1.00")
    min_average_daily_dollar_volume: Decimal = Decimal("5000000")
    max_missing_bar_pct: float = 0.30
    max_symbols: int = 500
    asset_type: str = "us_equity"
    weight_dollar_volume: float = 0.50
    weight_trading_consistency: float = 0.20
    weight_bar_completeness: float = 0.20
    weight_volatility_penalty: float = 0.10


@dataclass(frozen=True)
class UniverseRebalanceConfig:
    target_universe_size: int = 500
    max_churn_pct: float = 0.15
    retain_until_rank_greater_than: int = 650
    add_only_if_rank_less_than_or_equal: int = 400
    min_candidate_rank_to_include: int = 1
    force_rebalance: bool = False
    allow_empty_active_universe_bootstrap: bool = True
