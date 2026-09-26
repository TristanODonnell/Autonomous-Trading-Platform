"""Delisting detection (bar gaps) and what the replay does with a detected delisting."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEventType,
)
from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    SimulatedBrokerClient,
)
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    resolve_trading_universe,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import partition_file_path
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import (
    SymbolDateCoverage,
)
from autonomous_trading_platform.storage.sor.repositories.core.symbol_date_coverage_repository import (
    SymbolDateCoverageRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.universe.services.delisting_detection_service import (
    DelistingDetectionService,
    find_delisted_symbols,
)
from tests.utilities.universe_seeding import seed_universe_version

_DV = "delisting_test_v1"


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _weekdays(date(2023, 3, 1), 15)


class TestFindDelistedSymbols:
    def test_symbol_silent_for_grace_market_days_is_delisted(self) -> None:
        found = find_delisted_symbols(
            last_bar_dates={"LIVE": DAYS[-1], "DEAD": DAYS[6]},
            market_dates=DAYS,
            today=DAYS[-1],
            grace_trading_days=5,
        )
        assert [(d.symbol, d.last_bar_date, d.missing_market_days) for d in found] == [
            ("DEAD", DAYS[6], 8)
        ]
        assert found[0].effective_at == datetime.combine(
            DAYS[6] + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )

    def test_short_gap_is_not_a_delisting(self) -> None:
        found = find_delisted_symbols(
            last_bar_dates={"HALTED": DAYS[-3]},
            market_dates=DAYS,
            today=DAYS[-1],
            grace_trading_days=5,
        )
        assert found == []

    def test_market_closed_days_do_not_count(self) -> None:
        # Only 2 market-open days after the last bar, even though a week passed.
        found = find_delisted_symbols(
            last_bar_dates={"S": DAYS[0]},
            market_dates=[DAYS[0], DAYS[5], DAYS[6]],
            today=DAYS[10],
            grace_trading_days=3,
        )
        assert found == []

    def test_future_market_dates_ignored(self) -> None:
        found = find_delisted_symbols(
            last_bar_dates={"S": DAYS[0]},
            market_dates=DAYS,
            today=DAYS[2],
            grace_trading_days=3,
        )
        assert found == []

    def test_grace_validation(self) -> None:
        with pytest.raises(ValueError):
            find_delisted_symbols(
                last_bar_dates={}, market_dates=[], today=DAYS[0], grace_trading_days=0
            )


def _coverage(session, symbol: str, day: date, bars: int) -> None:
    session.add(
        SymbolDateCoverage(
            coverage_id=f"{_DV}:{symbol}:{day.isoformat()}",
            symbol=symbol,
            date=day,
            dataset_version=_DV,
            expected_bar_count=1,
            actual_bar_count=bars,
            completeness_status="complete" if bars else "missing",
            gap_summary=None,
            updated_at=datetime.now(UTC),
        )
    )


@pytest.fixture()
def dead_after_day_6(db_session):
    """LIVE trades every day; DEAD's bars stop after DAYS[6]; NEVER never traded."""
    for i, day in enumerate(DAYS):
        _coverage(db_session, "LIVE", day, 78)
        _coverage(db_session, "DEAD", day, 78 if i <= 6 else 0)
        _coverage(db_session, "NEVER", day, 0)
    db_session.flush()
    return db_session


class TestDelistingDetectionService:
    def test_records_event_once(self, dead_after_day_6) -> None:
        session = dead_after_day_6
        service = DelistingDetectionService(
            coverage_repository=SymbolDateCoverageRepository(session),
            lifecycle_repository=TickerLifecycleRepository(session),
        )
        first = service.detect_and_record(
            dataset_version=_DV, symbols=["LIVE", "DEAD", "NEVER"], today=DAYS[-1]
        )
        again = service.detect_and_record(
            dataset_version=_DV, symbols=["LIVE", "DEAD", "NEVER"], today=DAYS[-1]
        )

        assert [d.symbol for d in first] == ["DEAD"]
        assert again == []  # idempotent
        events = TickerLifecycleRepository(session).list_events_for_symbol("DEAD")
        assert len(events) == 1
        assert events[0].event_type == TickerLifecycleEventType.DELISTING
        assert events[0].effective_at.date() == DAYS[6] + timedelta(days=1)

    def test_not_detected_before_grace_elapses(self, dead_after_day_6) -> None:
        service = DelistingDetectionService(
            coverage_repository=SymbolDateCoverageRepository(dead_after_day_6),
            lifecycle_repository=TickerLifecycleRepository(dead_after_day_6),
        )
        assert service.detect_and_record(dataset_version=_DV, symbols=["DEAD"], today=DAYS[9]) == []


class TestTradingUniverseDropsDelisted:
    def test_delisted_member_removed_after_effective_date(self, dead_after_day_6) -> None:
        session = dead_after_day_6
        seed_universe_version(
            session,
            symbols=["LIVE", "DEAD"],
            effective_from=datetime(2023, 2, 1, tzinfo=UTC),
        )
        DelistingDetectionService(
            coverage_repository=SymbolDateCoverageRepository(session),
            lifecycle_repository=TickerLifecycleRepository(session),
        ).detect_and_record(dataset_version=_DV, symbols=["LIVE", "DEAD"], today=DAYS[-1])

        before, *_ = resolve_trading_universe(
            session=session, now_utc=datetime.combine(DAYS[6], datetime.min.time(), tzinfo=UTC)
        )
        after, *_ = resolve_trading_universe(
            session=session, now_utc=datetime.combine(DAYS[-1], datetime.min.time(), tzinfo=UTC)
        )
        assert before == {"LIVE", "DEAD"}
        assert after == {"LIVE"}


def _write_daily_bars(root: Path, symbol: str, closes: dict[date, float]) -> None:
    now = datetime(2023, 1, 1, tzinfo=UTC)
    rows = []
    for d, close in sorted(closes.items()):
        ts = datetime(d.year, d.month, d.day, 20, 55, tzinfo=UTC)
        rows.append(
            {
                "bar_id": f"{symbol}_{d.isoformat()}",
                "timestamp": ts,
                "end_timestamp": ts + timedelta(minutes=5),
                "interval": BarInterval.FIVE_MIN.value,
                "symbol": symbol,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 50_000,
                "vwap": close,
                "trade_count": 100,
                "price_basis": PriceBasis.RAW.value,
                "adjustment_factor": 1.0,
                "source": "test",
                "ingested_at": now,
                "quality_flags": None,
                "date": d,
                "year": f"{d.year:04d}",
                "month": f"{d.month:02d}",
            }
        )
    path = partition_file_path(
        base_path=root,
        dataset=RAW_BARS_DATASET,
        dataset_version=_DV,
        partitions={"symbol": symbol, "year": "2023", "month": "03"},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({f.name: [r.get(f.name) for r in rows] for f in BAR_SCHEMA}, schema=BAR_SCHEMA),
        path,
    )


class TestBrokerExitsDelistedHolding:
    def _broker(self, session, root: Path, when: datetime) -> SimulatedBrokerClient:
        execution = SimulatedExecutionService(
            simulation_cost_model_service=SimulationCostModelService(
                config=SimulationCostModelConfig(
                    commission_per_share=Decimal("0"), min_commission=Decimal("0")
                ),
                slippage_model=SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0"))),
            ),
            fill_model_config=SimulatedFillModelConfig(),
        )
        execution.reset_for_run(rng=random.Random(1))
        return SimulatedBrokerClient(
            session=session,
            timestamp=when,
            simulated_execution_service=execution,
            base_path=str(root),
            dataset_version_id=_DV,
        )

    def test_delisted_symbol_priced_and_sold_at_last_close(
        self, dead_after_day_6, tmp_path: Path
    ) -> None:
        session = dead_after_day_6
        _write_daily_bars(tmp_path, "DEAD", {d: 50.0 + i for i, d in enumerate(DAYS[:7])})
        after = datetime.combine(DAYS[-1], datetime.min.time(), tzinfo=UTC).replace(hour=20)

        # Before the delisting is recorded there is no price: exit would be skipped.
        assert self._broker(session, tmp_path, after).get_latest_trades(["DEAD"]) == {}

        DelistingDetectionService(
            coverage_repository=SymbolDateCoverageRepository(session),
            lifecycle_repository=TickerLifecycleRepository(session),
        ).detect_and_record(dataset_version=_DV, symbols=["DEAD"], today=DAYS[-1])

        broker = self._broker(session, tmp_path, after)
        assert broker.get_latest_trades(["DEAD"])["DEAD"]["p"] == 56.0  # last close
        order = broker.submit_order(
            {
                "symbol": "DEAD",
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "qty": "10",
            }
        )
        assert order["status"] == "filled"
        assert Decimal(order["filled_avg_price"]) == Decimal("56.0")

    def test_live_symbol_without_bar_is_not_force_priced(
        self, dead_after_day_6, tmp_path: Path
    ) -> None:
        when = datetime.combine(DAYS[-1], datetime.min.time(), tzinfo=UTC).replace(hour=20)
        assert self._broker(dead_after_day_6, tmp_path, when).get_latest_trades(["LIVE"]) == {}
