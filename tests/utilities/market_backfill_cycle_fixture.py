from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

BACKFILL_NOW_UTC = datetime(2025, 3, 31, 21, 0, tzinfo=UTC)
BACKFILL_START_UTC = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
BACKFILL_END_UTC = datetime(2025, 1, 3, 21, 0, tzinfo=UTC)

BACKFILL_SYMBOLS = ["SPY", "AAPL", "MSFT"]


@dataclass(frozen=True)
class SeededMarketBackfillCycleFixture:
    now_utc: datetime
    start: datetime
    end: datetime
    symbols: list[str]
    symbol_count: int
    data_root: Path


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


class FakeRawHistoricalClient:
    pass


class FakeAlpacaHistoricalBarsClient:
    def __init__(self, _raw_client: Any):
        pass

    def fetch_bars(self, *, symbols, start, end, timeframe="1Min", **kwargs):
        data = {}

        for symbol_index, symbol in enumerate(symbols):
            base = 100.0 + symbol_index * 10
            bars = []

            for i in range(10):
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

        all_bars = []

        for symbol_bars in data.values():
            all_bars.extend(symbol_bars)

        return all_bars


def seed_market_backfill_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededMarketBackfillCycleFixture:
    _seed_environment(monkeypatch)
    _patch_runtime_seams(
        session=session,
        data_root=data_root,
        monkeypatch=monkeypatch,
    )

    return SeededMarketBackfillCycleFixture(
        now_utc=BACKFILL_NOW_UTC,
        start=BACKFILL_START_UTC,
        end=BACKFILL_END_UTC,
        symbols=BACKFILL_SYMBOLS,
        symbol_count=len(BACKFILL_SYMBOLS),
        data_root=data_root,
    )


def _seed_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DATA_ROOT", "")
    monkeypatch.setenv("PARQUET_DATA_ROOT", "")
    monkeypatch.setenv("LOCAL_DATA_ROOT", "")


def _patch_runtime_seams(*, session: Session, data_root: Path, monkeypatch) -> None:
    import autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle as cycle_module
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    monkeypatch.setattr(cycle_module, "get_session", lambda: session)

    monkeypatch.setattr(
        parquet_paths,
        "get_data_root",
        lambda: data_root,
        raising=False,
    )

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

    monkeypatch.setattr(
        cycle_module,
        "get_stock_historical_client",
        lambda: FakeRawHistoricalClient(),
    )

    monkeypatch.setattr(
        cycle_module,
        "AlpacaHistoricalBarsClient",
        FakeAlpacaHistoricalBarsClient,
    )
