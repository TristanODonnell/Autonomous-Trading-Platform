from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from tests.utilities.market_ingestion_cycle_fixture import (
    _patch_runtime_seams as _patch_ingestion_runtime_seams,
)
from tests.utilities.paper_trading_cycle_fixture import (
    SeededPaperTradingCycleFixture,
    seed_paper_trading_cycle_fixture,
)


@dataclass(frozen=True)
class SeededPaperTradingGoldenPathFixture:
    now_utc: datetime
    dataset_version: str
    feature_dataset_version: str
    account_id: str
    symbol: str
    starting_cash: Decimal
    starting_position: Decimal
    data_root: Path
    strategy_ids: tuple[str, ...]


def seed_paper_trading_golden_path_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededPaperTradingGoldenPathFixture:
    _patch_ingestion_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )

    cycle_fixture: SeededPaperTradingCycleFixture = seed_paper_trading_cycle_fixture(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )

    return SeededPaperTradingGoldenPathFixture(
        now_utc=cycle_fixture.now_utc,
        dataset_version=cycle_fixture.dataset_version,
        feature_dataset_version=cycle_fixture.feature_dataset_version,
        account_id=cycle_fixture.account_id,
        symbol=cycle_fixture.symbol,
        starting_cash=cycle_fixture.starting_cash,
        starting_position=cycle_fixture.starting_position,
        data_root=cycle_fixture.data_root,
        strategy_ids=cycle_fixture.strategy_ids,
    )
