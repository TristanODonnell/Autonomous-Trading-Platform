"""ResearchWindowDatasetService: backfill versions merged into one checked version."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.research_window_dataset_service import (
    ResearchWindowDatasetService,
)
from autonomous_trading_platform.application.services.rolling_raw_bars_service import (
    BACKFILL_DATASET_TYPE,
    RollingRawBarsService,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    RESEARCH_WINDOW_LIFECYCLE,
    RESEARCH_WINDOW_SOURCE,
    ResearchWindowDatasetResult,
    SessionShape,
)
from autonomous_trading_platform.runtime.clock import FakeMarketCalendar
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

_NEW_YORK = ZoneInfo("America/New_York")
_NOW = datetime(2026, 11, 16, 22, tzinfo=UTC)
# Monday to Friday; the fake calendar trades every weekday, 09:30 to 16:00: 78 bars.
MON, TUE, WED, THU, FRI = (date(2024, 10, d) for d in (7, 8, 9, 10, 11))
WEEK = [MON, TUE, WED, THU, FRI]
FULL = 78


def _session_bars(symbol: str, day: date, *, bars: int = FULL, close: float = 100.0) -> pa.Table:
    opened = datetime.combine(day, time(9, 30), tzinfo=_NEW_YORK).astimezone(UTC)
    stamps = [opened + timedelta(minutes=5 * i) for i in range(bars)]
    closes = [close] * bars
    utc = pa.timestamp("us", tz="UTC")
    return pa.table(
        {
            "bar_id": pa.array([f"{symbol}-{s.isoformat()}" for s in stamps], type=pa.string()),
            "timestamp": pa.array(stamps, type=utc),
            "end_timestamp": pa.array([s + timedelta(minutes=5) for s in stamps], type=utc),
            "interval": pa.array(["5m"] * bars, type=pa.string()),
            "symbol": pa.array([symbol] * bars, type=pa.string()),
            "open": pa.array(closes, type=pa.float64()),
            "high": pa.array(closes, type=pa.float64()),
            "low": pa.array(closes, type=pa.float64()),
            "close": pa.array(closes, type=pa.float64()),
            "volume": pa.array([1000] * bars, type=pa.int64()),
            "vwap": pa.array(closes, type=pa.float64()),
            "trade_count": pa.array([10] * bars, type=pa.int64()),
            "price_basis": pa.array(["raw"] * bars, type=pa.string()),
            "adjustment_factor": pa.array([1.0] * bars, type=pa.float64()),
            "source": pa.array(["test"] * bars, type=pa.string()),
            "ingested_at": pa.array(stamps, type=utc),
            "quality_flags": pa.array([None] * bars, type=pa.list_(pa.string())),
        }
    )


def _seed_chunk(
    session: Session,
    base_path: Path,
    *,
    version_id: str,
    start: date,
    end: date,
    tables: list[pa.Table],
    symbols: list[str] | None = None,
) -> str:
    """A version as ``atp ingestion run-backfill`` registers it."""
    write_table(pa.concat_tables(tables), RAW_BARS_DATASET, base_path, version_id)
    declared = symbols or sorted({t.column("symbol")[0].as_py() for t in tables})
    session.add(
        DatasetVersions(
            dataset_version_id=version_id,
            dataset_name="raw_bars",
            created_at=datetime(end.year, end.month, end.day, 23, tzinfo=UTC),
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            symbol_coverage=len(declared),
            date_coverage_start=start,
            date_coverage_end=end,
            validation_status="validated",
            checksum="x",
            source_manifest={"pipeline": "market_backfill", "symbols": declared},
            metadata_json={"dataset_type": BACKFILL_DATASET_TYPE},
        )
    )
    session.flush()
    return version_id


def _week(symbols: list[str], days: list[date], **overrides: pa.Table) -> list[pa.Table]:
    """Full sessions for every symbol and day; ``overrides`` replaces one, keyed
    ``"<symbol>_<day of month>"``. An empty override table leaves the session out."""
    tables = []
    for symbol in symbols:
        for day in days:
            table = overrides.get(f"{symbol}_{day.day}", _session_bars(symbol, day))
            if table.num_rows:
                tables.append(table)
    return tables


_NO_BARS = _session_bars("X", MON, bars=0)


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _build(
    session: Session,
    base_path: Path,
    *,
    start: date = MON,
    end: date = FRI,
    symbols: tuple[str, ...] = ("AAA", "BBB"),
    calendar: FakeMarketCalendar | None = None,
    plan_only: bool = False,
    exclude: tuple[date, ...] = (),
) -> ResearchWindowDatasetResult:
    return ResearchWindowDatasetService(session, base_path=base_path).build_window(
        start=start,
        end=end,
        symbols=symbols,
        now_utc=_NOW,
        calendar=calendar or FakeMarketCalendar(),
        plan_only=plan_only,
        exclude_sessions=exclude,
    )


def _window_rows(session: Session) -> list[DatasetVersions]:
    return [
        row
        for row in session.query(DatasetVersions).all()
        if (row.metadata_json or {}).get("dataset_lifecycle") == RESEARCH_WINDOW_LIFECYCLE
    ]


def _version_dirs(base_path: Path) -> set[str]:
    return {p.name for p in base_path.rglob("dataset_version=*") if p.is_dir()}


def _two_chunks(session: Session, base_path: Path, **overrides: pa.Table) -> list[str]:
    names = ["AAA", "BBB"]
    return [
        _seed_chunk(
            session,
            base_path,
            version_id="chunk_1",
            start=MON,
            end=WED,
            tables=_week(names, [MON, TUE, WED], **overrides),
            symbols=names,
        ),
        _seed_chunk(
            session,
            base_path,
            version_id="chunk_2",
            start=THU,
            end=FRI,
            tables=_week(names, [THU, FRI], **overrides),
            symbols=names,
        ),
    ]


def test_chunks_become_one_validated_version(db_session: Session, base_path: Path) -> None:
    chunks = _two_chunks(db_session, base_path)

    result = _build(db_session, base_path)

    assert result.status == "created"
    assert result.errors == []
    assert result.source_versions == chunks
    assert result.rows == 2 * 5 * FULL
    assert result.sessions_expected == 5
    assert result.symbol_coverage == {"AAA": 1.0, "BBB": 1.0}
    assert result.symbol_sessions_missing == {}
    assert result.price_jumps == []

    (row,) = _window_rows(db_session)
    assert row.dataset_version_id == result.dataset_version_id
    assert row.validation_status == "validated"
    assert row.source == RESEARCH_WINDOW_SOURCE
    assert (row.date_coverage_start, row.date_coverage_end) == (MON, FRI)
    assert row.symbol_coverage == 2
    assert row.source_manifest["symbols"] == ["AAA", "BBB"]
    assert row.metadata_json["source_versions"] == chunks
    assert row.metadata_json["window_start"] == MON.isoformat()

    stored = HistoricalBarDatasetReader(db_session, base_path=base_path).read(
        dataset=RAW_BARS_DATASET,
        dataset_version=row.dataset_version_id,
        symbol="AAA",
        start_date=MON,
        end_date=FRI,
    )
    assert stored.num_rows == 5 * FULL
    # The bench window looks for its own tag and must not see this version.
    assert RollingRawBarsService(db_session, base_path=base_path).latest_rolling() is None


def test_only_the_window_and_the_requested_names_are_kept(
    db_session: Session, base_path: Path
) -> None:
    names = ["AAA", "BBB", "CCC"]
    _seed_chunk(
        db_session,
        base_path,
        version_id="wide",
        start=MON,
        end=FRI,
        tables=_week(names, WEEK),
        symbols=names,
    )

    result = _build(db_session, base_path, start=TUE, end=WED, symbols=("aaa",))

    assert result.status == "created"
    assert result.symbols == ["AAA"]
    assert result.rows == 2 * FULL
    (row,) = _window_rows(db_session)
    assert (row.date_coverage_start, row.date_coverage_end) == (TUE, WED)


def test_a_session_with_no_bars_refuses_and_writes_nothing(
    db_session: Session, base_path: Path
) -> None:
    _two_chunks(db_session, base_path, AAA_9=_NO_BARS, BBB_9=_NO_BARS)
    before = _version_dirs(base_path)

    result = _build(db_session, base_path)

    assert result.status == "refused"
    assert result.dataset_version_id is None
    assert result.sessions_missing == [WED]
    assert "1 session(s) have no bars for any symbol" in result.errors[0]
    assert _window_rows(db_session) == []
    assert _version_dirs(base_path) == before


def test_a_session_short_of_its_bar_times_refuses(db_session: Session, base_path: Path) -> None:
    short = {f"{name}_8": _session_bars(name, TUE, bars=FULL - 1) for name in ("AAA", "BBB")}
    _two_chunks(db_session, base_path, **short)

    result = _build(db_session, base_path)

    assert result.status == "refused"
    assert result.sessions_off_shape == [SessionShape(session=TUE, bar_times=77, expected=78)]
    assert "2024-10-08 (77 of 78)" in result.errors[0]
    assert _window_rows(db_session) == []


def test_a_half_day_is_measured_against_its_own_hours(db_session: Session, base_path: Path) -> None:
    half = {f"{name}_9": _session_bars(name, WED, bars=42) for name in ("AAA", "BBB")}
    _two_chunks(db_session, base_path, **half)

    result = _build(
        db_session, base_path, calendar=FakeMarketCalendar(early_closes={WED: time(13, 0)})
    )

    assert result.status == "created"
    assert result.sessions_off_shape == []


def test_one_name_missing_a_session_is_reported_not_refused(
    db_session: Session, base_path: Path
) -> None:
    _two_chunks(db_session, base_path, BBB_8=_NO_BARS)

    result = _build(db_session, base_path)

    assert result.status == "created"
    assert result.symbol_sessions_missing == {"BBB": 1}
    assert result.symbol_coverage == {"AAA": 1.0, "BBB": 0.8}


def test_a_split_sized_jump_in_raw_prices_is_reported(db_session: Session, base_path: Path) -> None:
    after = {f"AAA_{day.day}": _session_bars("AAA", day, close=10.0) for day in (WED, THU, FRI)}
    _two_chunks(db_session, base_path, **after)

    result = _build(db_session, base_path)

    assert result.status == "created"
    (jump,) = result.price_jumps
    assert jump.symbol == "AAA"
    assert (jump.previous_close, jump.close, jump.change) == (100.0, 10.0, -0.9)
    assert jump.timestamp == datetime.combine(WED, time(9, 30), tzinfo=_NEW_YORK).astimezone(UTC)
    (row,) = _window_rows(db_session)
    assert row.metadata_json["price_jumps"] == 1


def test_a_version_without_every_requested_name_is_left_out(
    db_session: Session, base_path: Path
) -> None:
    _seed_chunk(
        db_session,
        base_path,
        version_id="one_name",
        start=MON,
        end=WED,
        tables=_week(["AAA"], [MON, TUE, WED]),
    )
    _seed_chunk(
        db_session,
        base_path,
        version_id="both_names",
        start=THU,
        end=FRI,
        tables=_week(["AAA", "BBB"], [THU, FRI]),
    )

    result = _build(db_session, base_path)

    assert result.status == "refused"
    assert result.source_versions == ["both_names"]
    assert result.sessions_missing == [MON, TUE, WED]
    assert "in no backfill version that holds every requested symbol" in result.errors[0]
    assert _window_rows(db_session) == []


def test_the_same_inputs_are_not_built_twice(db_session: Session, base_path: Path) -> None:
    _two_chunks(db_session, base_path)
    first = _build(db_session, base_path)

    second = _build(db_session, base_path)

    assert second.status == "up_to_date"
    assert second.dataset_version_id == first.dataset_version_id
    assert len(_window_rows(db_session)) == 1
    # Another window over the same chunks is a different dataset.
    assert _build(db_session, base_path, start=TUE).status == "created"


def test_plan_lists_the_sources_and_writes_nothing(db_session: Session, base_path: Path) -> None:
    chunks = _two_chunks(db_session, base_path)
    before = _version_dirs(base_path)

    result = _build(db_session, base_path, plan_only=True)

    assert result.status == "planned"
    assert result.source_versions == chunks
    assert result.sessions_expected == 5
    assert result.rows == 0
    assert _window_rows(db_session) == []
    assert _version_dirs(base_path) == before


def _one_empty_one_short(session: Session, base_path: Path) -> None:
    """Tuesday has 10 bar times, Wednesday has no bars: the two faults seen in the feed."""
    faulty = {f"{name}_8": _session_bars(name, TUE, bars=10) for name in ("AAA", "BBB")}
    faulty.update({"AAA_9": _NO_BARS, "BBB_9": _NO_BARS})
    _two_chunks(session, base_path, **faulty)


def test_faulty_sessions_left_out_by_name_are_dropped_and_recorded(
    db_session: Session, base_path: Path
) -> None:
    _one_empty_one_short(db_session, base_path)
    assert _build(db_session, base_path).status == "refused"

    result = _build(db_session, base_path, exclude=(WED, TUE))

    assert result.status == "created"
    assert result.sessions_excluded == [TUE, WED]
    assert result.sessions_expected == 3
    assert result.sessions_missing == []
    assert result.sessions_off_shape == []
    # Tuesday's ten bars are gone as well: a left-out session keeps nothing.
    assert result.rows == 2 * 3 * FULL
    assert result.symbol_coverage == {"AAA": 1.0, "BBB": 1.0}
    (row,) = _window_rows(db_session)
    assert row.metadata_json["excluded_sessions"] == ["2024-10-08", "2024-10-09"]
    assert row.source_manifest["excluded_sessions"] == ["2024-10-08", "2024-10-09"]
    assert row.metadata_json["sessions"] == 3
    stored = HistoricalBarDatasetReader(db_session, base_path=base_path).read(
        dataset=RAW_BARS_DATASET,
        dataset_version=row.dataset_version_id,
        symbol="AAA",
        start_date=TUE,
        end_date=WED,
    )
    assert stored.num_rows == 0


def test_leaving_out_a_complete_session_is_refused(db_session: Session, base_path: Path) -> None:
    _one_empty_one_short(db_session, base_path)
    before = _version_dirs(base_path)

    result = _build(db_session, base_path, exclude=(TUE, WED, THU))

    assert result.status == "refused"
    assert "1 excluded session(s) have every bar time" in result.errors[0]
    assert "2024-10-10" in result.errors[0]
    assert _window_rows(db_session) == []
    assert _version_dirs(base_path) == before


def test_the_exclusion_list_is_part_of_what_makes_a_dataset(
    db_session: Session, base_path: Path
) -> None:
    _one_empty_one_short(db_session, base_path)
    first = _build(db_session, base_path, exclude=(TUE, WED))

    again = _build(db_session, base_path, exclude=(WED, TUE))
    without = _build(db_session, base_path)

    assert again.status == "up_to_date"
    assert again.dataset_version_id == first.dataset_version_id
    # Without the list the same chunks are still the faulty dataset, not the stored one.
    assert without.status == "refused"


@pytest.mark.parametrize(
    ("exclude", "message"),
    [
        ((date(2024, 10, 12),), "2024-10-12 is not a trading session"),
        ((date(2024, 10, 14),), "2024-10-14 is not a trading session"),
        ((MON, TUE, WED, THU, FRI), "every session in the window is excluded"),
    ],
)
def test_a_bad_exclusion_list_is_an_error(
    db_session: Session, base_path: Path, exclude: tuple[date, ...], message: str
) -> None:
    _two_chunks(db_session, base_path)

    with pytest.raises(ValueError, match=message):
        _build(db_session, base_path, exclude=exclude)


@pytest.mark.parametrize(
    ("start", "end", "symbols", "message"),
    [
        (FRI, MON, ("AAA",), "is before --start"),
        (MON, FRI, (" ", ""), "at least one symbol"),
        (date(2024, 10, 12), date(2024, 10, 13), ("AAA",), "no trading session"),
    ],
)
def test_bad_requests_are_errors(
    db_session: Session,
    base_path: Path,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _build(db_session, base_path, start=start, end=end, symbols=symbols)
