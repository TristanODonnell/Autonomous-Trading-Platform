from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

INGESTION_SYMBOL_COUNT = 250
INGESTION_NOW_UTC = datetime(2025, 2, 14, 21, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SeededMarketIngestionCycleFixture:
    now_utc: datetime
    symbols: list[str]
    symbol_count: int
    data_root: Path


class FakeAlpacaBarsResponse:
    def __init__(self, data):
        self.data = data


def _fake_symbols() -> list[str]:
    return [f"TST{i:04d}" for i in range(INGESTION_SYMBOL_COUNT)]


@dataclass(frozen=True)
class FakeProviderBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float
    trade_count: int


class FakeOneMinuteMarketDataClient:
    """
    Simulates real provider returning 1-minute bars.
    5 bars per symbol → should aggregate into 1 five-minute bar.
    """

    def __init__(self, _settings: Any = None) -> None:
        pass

    def get_bars(self, symbols, start, end, timeframe="1Min"):
        rows = []

        for symbol_index, symbol in enumerate(symbols):
            base = 100.0 + symbol_index

            for i in range(5):
                ts = start + timedelta(minutes=i)

                open_price = base + i
                close_price = open_price + 0.5

                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": ts,
                        "open": open_price,
                        "high": close_price + 0.25,
                        "low": open_price - 0.25,
                        "close": close_price,
                        "volume": 100 + i,
                        "vwap": close_price,
                        "trade_count": 10 + i,
                    }
                )

        return rows


def _patch_runtime_seams(*, session: Session, data_root: Path, monkeypatch):

    import autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle as cycle_module
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(
        parquet_paths,
        "get_data_root",
        lambda: data_root,
        raising=False,
    )
    # (optional safety if settings-based)
    try:
        from autonomous_trading_platform.config.settings import Settings

        monkeypatch.setattr(
            Settings,
            "DATA_ROOT",
            str(data_root),
            raising=False,
        )
    except Exception:
        pass

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(data_root))

    # Force DB session
    monkeypatch.setattr(cycle_module, "get_session", lambda: session)

    # Patch universe → 250 symbols
    monkeypatch.setattr(
        cycle_module.UniverseMembershipService,
        "get_query_symbols_for_date",
        lambda _self, _date: _fake_symbols(),
    )

    # Patch data client inside ingestion job
    def fake_fetch_minute_bars(*, symbols, start, end, **kwargs):
        data = {}

        for symbol_index, symbol in enumerate(symbols):
            base = 100.0 + symbol_index
            bars = []

            for i in range(5):
                ts = start + timedelta(minutes=i)

                open_price = base + i
                close_price = open_price + 0.5

                bars.append(
                    FakeProviderBar(
                        symbol=symbol,
                        timestamp=ts,
                        open=open_price,
                        high=close_price + 0.25,
                        low=open_price - 0.25,
                        close=close_price,
                        volume=100 + i,
                        vwap=close_price,
                        trade_count=10 + i,
                    )
                )

            data[symbol] = bars

        return FakeAlpacaBarsResponse(data)

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client.fetch_minute_bars",
        fake_fetch_minute_bars,
    )

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job.fetch_minute_bars",
        fake_fetch_minute_bars,
        raising=False,
    )

    from autonomous_trading_platform.ingestion.market_data.services.bar_validation_service import (
        BarValidationService,
    )

    monkeypatch.setattr(BarValidationService, "is_late_bar", lambda *_args, **_kwargs: False)


def seed_market_ingestion_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededMarketIngestionCycleFixture:
    _patch_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )

    return SeededMarketIngestionCycleFixture(
        now_utc=INGESTION_NOW_UTC,
        symbols=_fake_symbols(),
        symbol_count=INGESTION_SYMBOL_COUNT,
        data_root=data_root,
    )
