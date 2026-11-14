"""EOD ``final_ingestion`` stores the session's last five-minute bar (Finding 28).

No intraday tick can store it: a tick fetches the window that ended before the tick
started, and the last tick starts before the close. The step used to ask for the window
ending at its own clock (17:55-18:00 ET), which is outside the session, so every live day
ended one bar short.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pyarrow.dataset as ds
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.clock import FakeMarketCalendar
from autonomous_trading_platform.scheduler.orchestration import (
    paper_trading_golden_path_orchestrator as orchestrator_module,
)
from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import ChainContext
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    EOD_CHAIN_JOB_NAME,
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from tests.utilities.market_ingestion_cycle_fixture import (
    FakeAlpacaBarsResponse,
    FakeProviderBar,
    SeededMarketIngestionCycleFixture,
)

# The ingestion fixture's day: Friday 2025-02-14, a full session (EST, so ET = UTC-5).
SESSION = date(2025, 2, 14)
CHAIN_CLOCK = datetime(2025, 2, 14, 23, 0, tzinfo=UTC)  # 18:00 ET, when the chain runs
LAST_BAR = datetime(2025, 2, 14, 20, 55, tzinfo=UTC)  # 15:55 ET
CLOSE = datetime(2025, 2, 14, 21, 0, tzinfo=UTC)  # 16:00 ET


def _context(session: Session, trading_date: date, now_utc: datetime) -> ChainContext:
    return ChainContext(
        chain_name=EOD_CHAIN_JOB_NAME,
        trading_date=trading_date,
        now_utc=now_utc,
        session=session,
        parent_job_run_id="parent-1",
    )


def _stored_bar_times(
    db_session: Session, fixture: SeededMarketIngestionCycleFixture
) -> list[datetime]:
    version = db_session.query(DatasetVersions).order_by(DatasetVersions.created_at.desc()).first()
    assert version is not None
    root = dataset_version_root(fixture.data_root, RAW_BARS_DATASET, version.dataset_version_id)
    rows = ds.dataset(root, format="parquet", partitioning="hive").to_table().to_pylist()
    return [row["timestamp"].astimezone(UTC) for row in rows]


def test_final_ingestion_stores_the_closing_bar(
    seeded_market_ingestion_cycle_fixture: SeededMarketIngestionCycleFixture,
    db_session: Session,
) -> None:
    fixture = seeded_market_ingestion_cycle_fixture
    orchestrator = PaperTradingGoldenPathOrchestrator(db_session, calendar=FakeMarketCalendar())

    output = orchestrator._step_final_ingestion(_context(db_session, SESSION, CHAIN_CLOCK))

    times = _stored_bar_times(db_session, fixture)
    assert len(times) == fixture.symbol_count
    assert set(times) == {LAST_BAR}
    assert output == {
        "now_utc": CHAIN_CLOCK.isoformat(),
        "cycle_start": LAST_BAR.isoformat(),
        "cycle_end": CLOSE.isoformat(),
    }


def test_final_ingestion_survives_thin_names_with_no_closing_bar(
    seeded_market_ingestion_cycle_fixture: SeededMarketIngestionCycleFixture,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Most symbols printing nothing in the last five minutes is a quiet feed, not a dead
    one: the step keeps what there is instead of failing the chain."""
    fixture = seeded_market_ingestion_cycle_fixture
    traded = set(fixture.symbols[::5])

    def one_in_five(*, symbols: list[str], start: datetime, **_: Any) -> FakeAlpacaBarsResponse:
        return FakeAlpacaBarsResponse(
            {
                symbol: [
                    FakeProviderBar(
                        symbol=symbol,
                        timestamp=start + timedelta(minutes=minute),
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=100,
                        vwap=100.5,
                        trade_count=10,
                    )
                    for minute in range(5)
                ]
                for symbol in symbols
                if symbol in traded
            }
        )

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client.fetch_minute_bars",
        one_in_five,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job.fetch_minute_bars",
        one_in_five,
        raising=False,
    )
    orchestrator = PaperTradingGoldenPathOrchestrator(db_session, calendar=FakeMarketCalendar())

    orchestrator._step_final_ingestion(_context(db_session, SESSION, CHAIN_CLOCK))

    times = _stored_bar_times(db_session, fixture)
    assert len(times) == len(traded)
    assert set(times) == {LAST_BAR}


def test_final_ingestion_uses_the_early_close(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    black_friday = date(2026, 11, 27)  # NYSE closes at 13:00 ET
    chain_clock = datetime(2026, 11, 27, 23, 0, tzinfo=UTC)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        orchestrator_module, "run_market_ingestion_cycle", lambda **kwargs: calls.append(kwargs)
    )
    orchestrator = PaperTradingGoldenPathOrchestrator(
        db_session,
        calendar=FakeMarketCalendar(early_closes={black_friday: time(13, 0)}),
    )

    orchestrator._step_final_ingestion(_context(db_session, black_friday, chain_clock))

    assert calls == [
        {
            "now_utc": chain_clock,
            "cycle_start_override": datetime(2026, 11, 27, 17, 55, tzinfo=UTC),  # 12:55 ET
            "cycle_end_override": datetime(2026, 11, 27, 18, 0, tzinfo=UTC),  # 13:00 ET
            "enforce_lateness": False,
        }
    ]
