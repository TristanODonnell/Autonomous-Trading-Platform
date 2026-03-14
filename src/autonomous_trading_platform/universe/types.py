from dataclasses import dataclass
from typing import Protocol


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
