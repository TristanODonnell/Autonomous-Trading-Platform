from datetime import UTC, datetime

from alpaca.trading.client import TradingClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import UniverseSource
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_selection_service import (
    UniverseSelectionService,
)
from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
)
from autonomous_trading_platform.universe.services.universe_version_service import (
    UniverseVersionService,
)
from autonomous_trading_platform.universe.types import UniverseAsset


class AlpacaUniverseAssetSource:
    def __init__(self, client: TradingClient) -> None:
        self.client = client

    def list_assets(self) -> list[UniverseAsset]:
        assets = self.client.get_all_assets()

        return [
            UniverseAsset(
                symbol=asset.symbol,
                tradable=asset.tradable,
                status=str(asset.status),
                asset_class=str(asset.asset_class),
            )
            for asset in assets
        ]


def should_rebalance(now_utc: datetime, cadence: str) -> bool:
    if cadence == "daily":
        return True
    if cadence == "weekly":
        return now_utc.weekday() == 0
    raise ValueError(f"Unsupported cadence: {cadence}")


def run_universe_selection_cycle(*, cycle_timestamp: datetime | None = None) -> None:
    settings = Settings()
    api_key = settings.broker_api_key
    secret_key = settings.broker_api_secret
    cadence = settings.universe_rebalance_cadence

    session: Session = get_session()

    now_utc = cycle_timestamp or datetime.now(UTC)
    if not should_rebalance(now_utc, cadence):
        return

    trading_client = TradingClient(
        api_key,
        secret_key,
        paper=settings.trading_environment == TradingEnvironment.PAPER,
    )
    asset_source = AlpacaUniverseAssetSource(trading_client)

    selection_service = UniverseSelectionService(session, asset_source)
    version_repository = UniverseVersionRepository(session)
    version_service = UniverseVersionService(version_repository)
    validation_service = UniverseValidationService(session)

    selected_symbols, _criteria = selection_service.select_symbols(as_of=now_utc)

    if not selected_symbols:
        raise RuntimeError(
            "Universe selection produced zero symbols; refusing to create empty version"
        )

    version_name = f"universe_{now_utc.date().isoformat()}"
    version, members = version_service.build_version(
        name=version_name,
        effective_from=now_utc,
        symbols=selected_symbols,
        source=UniverseSource.CUSTOM,
        rebalance_reason=cadence,
    )

    validation = validation_service.validate_version_row(version, members)
    if not validation.ok:
        raise RuntimeError(validation.errors)

    version_repository.retire_active_version(version.effective_from)
    version_service.save_version(version, members)
    version_repository.activate_version(version.universe_version_id)
