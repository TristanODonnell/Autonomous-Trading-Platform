import os
from datetime import UTC, datetime

from alpaca.trading.client import TradingClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
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
from src.db import get_session


class AlpacaUniverseAssetSource:
    def __init__(self, client: TradingClient) -> None:
        self.client = client

    def list_assets(self) -> list[UniverseAsset]:
        assets = self.client.get_assets()
        return [
            UniverseAsset(
                symbol=asset.symbol,
                tradable=asset.tradable,
                status=str(asset.status),
                asset_class=str(asset.asset_class),
            )
            for asset in assets
        ]


def run_universe_selection_cycle() -> None:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    session: Session = get_session()

    trading_client = TradingClient(api_key, secret_key, paper=True)
    asset_source = AlpacaUniverseAssetSource(trading_client)

    selection_service = UniverseSelectionService(session, asset_source)

    snapshot_repository = UniverseSnapshotRepository(session)

    snapshot_service = UniverseSnapshotService(snapshot_repository)
    validation_service = UniverseValidationService(session)

    now_utc = datetime.now(UTC)

    selected_symbols, criteria = selection_service.select_symbols(as_of=now_utc)

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

    snapshot_service.save_snapshot(snapshot)
