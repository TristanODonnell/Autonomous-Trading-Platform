from datetime import UTC, datetime

from alpaca.trading.client import TradingClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.universe.services.universe_selection_service import (
    UniverseSelectionService,
)
from autonomous_trading_platform.universe.services.universe_snapshot_service import (
    UniverseSnapshotService,
)
from autonomous_trading_platform.universe.services.universe_validation_service import (
    UniverseValidationService,
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
    api_key = settings.paper_broker_api_key
    secret_key = settings.paper_broker_api_secret
    cadence = settings.universe_rebalance_cadence

    session: Session = get_session()

    now_utc = cycle_timestamp or datetime.now(UTC)
    if not should_rebalance(now_utc, cadence):
        return

    trading_client = TradingClient(api_key, secret_key, paper=True)
    asset_source = AlpacaUniverseAssetSource(trading_client)

    selection_service = UniverseSelectionService(session, asset_source)
    snapshot_repository = UniverseSnapshotRepository(session)
    snapshot_service = UniverseSnapshotService(snapshot_repository)
    validation_service = UniverseValidationService(session)

    selected_symbols, criteria = selection_service.select_symbols(as_of=now_utc)
    criteria["rebalance_cadence"] = cadence

    if not selected_symbols:
        raise RuntimeError(
            "Universe selection produced zero symbols; refusing to create empty snapshot"
        )

    snapshot = snapshot_service.build_snapshot(
        snapshot_date=now_utc.date(),
        effective_start=now_utc,
        symbols=selected_symbols,
        criteria=criteria,
        source="alpaca_universe_selection",
    )

    validation = validation_service.validate_row(snapshot)
    if not validation.ok:
        raise RuntimeError(validation.errors)

    snapshot_repository.close_open_snapshot(snapshot.effective_start)
    snapshot_service.save_snapshot(snapshot)
