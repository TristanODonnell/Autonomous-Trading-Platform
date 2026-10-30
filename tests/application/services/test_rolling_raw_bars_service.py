"""Rolling multi-session raw-bars version for the bench window (Finding 14)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

import autonomous_trading_platform.application.services.rolling_raw_bars_service as rolling_module
from autonomous_trading_platform.application.services.platform_replay import (
    bench_hooks,
    research_hooks,
)
from autonomous_trading_platform.application.services.rolling_raw_bars_service import (
    DAILY_LIFECYCLE,
    ROLLING_LIFECYCLE,
    RollingRawBarsService,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.checksums import Checksums
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

_NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


def _bars(symbol: str, day: date, closes: list[float], *, bar_prefix: str = "") -> pa.Table:
    n = len(closes)
    stamps = [
        datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * i)
        for i in range(n)
    ]
    return pa.table(
        {
            "bar_id": pa.array(
                [f"{bar_prefix}{symbol}-{day.isoformat()}-{i}" for i in range(n)], type=pa.string()
            ),
            "timestamp": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
            "end_timestamp": pa.array(
                [s + timedelta(minutes=5) for s in stamps], type=pa.timestamp("us", tz="UTC")
            ),
            "interval": pa.array(["5m"] * n, type=pa.string()),
            "symbol": pa.array([symbol] * n, type=pa.string()),
            "open": pa.array(closes, type=pa.float64()),
            "high": pa.array(closes, type=pa.float64()),
            "low": pa.array(closes, type=pa.float64()),
            "close": pa.array(closes, type=pa.float64()),
            "volume": pa.array([1000] * n, type=pa.int64()),
            "vwap": pa.array(closes, type=pa.float64()),
            "trade_count": pa.array([10] * n, type=pa.int64()),
            "price_basis": pa.array(["raw"] * n, type=pa.string()),
            "adjustment_factor": pa.array([1.0] * n, type=pa.float64()),
            "source": pa.array(["test"] * n, type=pa.string()),
            "ingested_at": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
            "quality_flags": pa.array([None] * n, type=pa.list_(pa.string())),
        }
    )


def seed_daily_version(
    session: Session,
    base_path: Path,
    *,
    day: date,
    tables: list[pa.Table],
    version_id: str | None = None,
    lifecycle: str = DAILY_LIFECYCLE,
    registered_hours_after_close: int = 0,
) -> str:
    version_id = version_id or f"raw_bars_{day.isoformat()}"
    write_table(pa.concat_tables(tables), RAW_BARS_DATASET, base_path, version_id)
    symbols = sorted({t.column("symbol")[0].as_py() for t in tables})
    session.add(
        DatasetVersions(
            dataset_version_id=version_id,
            dataset_name="raw_bars",
            created_at=datetime(day.year, day.month, day.day, 22, tzinfo=UTC)
            + timedelta(hours=registered_hours_after_close),
            source="ingestion",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            symbol_coverage=len(symbols),
            date_coverage_start=day,
            date_coverage_end=day,
            validation_status="validated",
            checksum="x",
            source_manifest={"symbols": symbols},
            metadata_json={"dataset_lifecycle": lifecycle},
        )
    )
    session.flush()
    return version_id


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


def test_merges_the_last_sessions_into_one_validated_version(
    db_session: Session, base_path: Path
) -> None:
    d1, d2, d3 = date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)
    seed_daily_version(db_session, base_path, day=d1, tables=[_bars("AAPL", d1, [1.0, 2.0])])
    seed_daily_version(
        db_session,
        base_path,
        day=d2,
        tables=[_bars("AAPL", d2, [3.0]), _bars("MSFT", d2, [10.0, 11.0, 12.0])],
    )
    seed_daily_version(db_session, base_path, day=d3, tables=[_bars("AAPL", d3, [4.0, 5.0])])

    service = RollingRawBarsService(db_session, base_path=base_path)
    result = service.build(as_of=d3, sessions=2, now_utc=_NOW)

    assert result.status == "created"
    assert result.dataset_version_id is not None
    assert result.source_versions == ["raw_bars_2026-10-23", "raw_bars_2026-10-26"]
    assert (result.coverage_start, result.coverage_end) == (d2, d3)
    assert result.symbols == ["AAPL", "MSFT"]
    assert result.rows == 6

    row = db_session.get(DatasetVersions, result.dataset_version_id)
    assert row is not None
    assert row.validation_status == "validated"
    assert row.metadata_json["dataset_lifecycle"] == ROLLING_LIFECYCLE
    assert row.metadata_json["source_versions"] == result.source_versions
    assert row.source_manifest["symbols"] == ["AAPL", "MSFT"]
    assert row.symbol_coverage == 2
    assert row.checksum

    # Readable across both sessions through the ordinary bounded reader, with checksums.
    reader = HistoricalBarDatasetReader(db_session, base_path=base_path)
    aapl = reader.read(
        dataset=RAW_BARS_DATASET,
        dataset_version=result.dataset_version_id,
        symbol="AAPL",
        start_date=d2,
        end_date=d3,
    )
    assert aapl.column("close").to_pylist() == [3.0, 4.0, 5.0]
    checksum_rows = (
        db_session.query(Checksums)
        .filter(Checksums.dataset_version == result.dataset_version_id)
        .all()
    )
    assert len(checksum_rows) >= 2  # one per written partition file

    # Same sources again: nothing new is written.
    again = service.build(as_of=d3, sessions=2, now_utc=_NOW + timedelta(days=1))
    assert again.status == "up_to_date"
    assert again.dataset_version_id == result.dataset_version_id

    # A new session lands: rebuilt from the newest two.
    d4 = date(2026, 10, 27)
    seed_daily_version(db_session, base_path, day=d4, tables=[_bars("AAPL", d4, [6.0])])
    rebuilt = service.build(as_of=d4, sessions=2, now_utc=_NOW + timedelta(days=1))
    assert rebuilt.status == "created"
    assert rebuilt.source_versions == ["raw_bars_2026-10-26", "raw_bars_2026-10-27"]
    latest = service.latest_rolling()
    assert latest is not None and latest.dataset_version_id == rebuilt.dataset_version_id


def test_duplicate_bar_ids_keep_the_newest_and_rolling_versions_are_not_sources(
    db_session: Session, base_path: Path
) -> None:
    d1, d2 = date(2026, 10, 22), date(2026, 10, 23)
    seed_daily_version(db_session, base_path, day=d1, tables=[_bars("AAPL", d1, [1.0, 2.0])])
    # A re-ingest of day 1 registered later with the same bar ids but corrected closes.
    seed_daily_version(
        db_session,
        base_path,
        day=d1,
        tables=[_bars("AAPL", d1, [1.5, 2.5])],
        version_id="raw_bars_2026-10-22_reingest",
        registered_hours_after_close=3,
    )
    seed_daily_version(db_session, base_path, day=d2, tables=[_bars("AAPL", d2, [3.0])])
    # An older rolling version must never feed a new one.
    seed_daily_version(
        db_session,
        base_path,
        day=d2,
        tables=[_bars("AAPL", d2, [99.0], bar_prefix="old-")],
        version_id="raw_bars_rolling_old",
        lifecycle=ROLLING_LIFECYCLE,
    )

    result = RollingRawBarsService(db_session, base_path=base_path).build(
        as_of=d2, sessions=5, now_utc=_NOW
    )

    assert result.status == "created"
    assert result.dataset_version_id is not None
    assert result.source_versions == ["raw_bars_2026-10-22_reingest", "raw_bars_2026-10-23"]
    assert result.rows == 3
    table = HistoricalBarDatasetReader(db_session, base_path=base_path).read(
        dataset=RAW_BARS_DATASET,
        dataset_version=result.dataset_version_id,
        symbol="AAPL",
        start_date=d1,
        end_date=d2,
    )
    assert table.column("close").to_pylist() == [1.5, 2.5, 3.0]


def test_skipped_without_daily_versions(db_session: Session, base_path: Path) -> None:
    result = RollingRawBarsService(db_session, base_path=base_path).build(
        as_of=date(2026, 10, 26), sessions=3, now_utc=_NOW
    )
    assert result.status == "skipped" and result.reason == "no_daily_versions"
    assert result.summary()["sessions_requested"] == 3


def test_bench_window_prefers_the_rolling_version(
    db_session: Session, base_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        research_hooks,
        "_resolve_research_universe_scope",
        lambda **kwargs: SimpleNamespace(member_symbols={"AAPL", "MSFT"}),
    )
    as_of = date(2026, 10, 26)
    days = [as_of - timedelta(days=n) for n in (40, 30, 20, 10, 0)]
    for day in days:
        seed_daily_version(db_session, base_path, day=day, tables=[_bars("AAPL", day, [1.0])])

    # Without a rolling version: the newest per-session version, as before.
    before = bench_hooks.resolve_bench_window(
        session=db_session, as_of=as_of, trading_days=63, replay_symbols=[]
    )
    assert before is not None
    assert before.dataset_version == f"raw_bars_{as_of.isoformat()}"
    assert before.start_date == as_of - timedelta(days=89)

    rolling = RollingRawBarsService(db_session, base_path=base_path).build(
        as_of=as_of, sessions=63, now_utc=_NOW
    )
    assert rolling.status == "created"

    window = bench_hooks.resolve_bench_window(
        session=db_session, as_of=as_of, trading_days=63, replay_symbols=[]
    )
    assert window is not None
    assert window.dataset_version == rolling.dataset_version_id
    # Clamped to what the rolling version actually holds.
    assert window.start_date == days[0]
    assert window.end_date == as_of
    assert window.symbols == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------------------
# Finding 15: on Postgres ``checksums.dataset_version`` is a foreign key to
# ``dataset_versions`` (migration 77afde93f3e2). SQLite does not enforce it, so the
# constraint is emulated here at flush time.


class _ForeignKeyViolation(Exception):
    pass


@pytest.fixture
def enforce_checksum_foreign_key(db_session: Session) -> Generator[None, None, None]:
    def _check(session: Session, flush_context: object, instances: object) -> None:
        pending = [obj for obj in session.new if isinstance(obj, Checksums)]
        if not pending:
            return
        with session.no_autoflush:
            registered = {
                row[0] for row in session.execute(select(DatasetVersions.dataset_version_id)).all()
            }
        missing = sorted({c.dataset_version for c in pending} - registered)
        if missing:
            raise _ForeignKeyViolation(
                f"checksums reference unregistered dataset_versions: {missing}"
            )

    event.listen(db_session, "before_flush", _check)
    try:
        yield
    finally:
        event.remove(db_session, "before_flush", _check)


def test_version_row_is_committed_before_its_checksum_rows(
    db_session: Session, base_path: Path, enforce_checksum_foreign_key: None
) -> None:
    d1, d2 = date(2026, 10, 22), date(2026, 10, 23)
    seed_daily_version(db_session, base_path, day=d1, tables=[_bars("AAPL", d1, [1.0, 2.0])])
    seed_daily_version(db_session, base_path, day=d2, tables=[_bars("AAPL", d2, [3.0])])

    result = RollingRawBarsService(db_session, base_path=base_path).build(
        as_of=d2, sessions=2, now_utc=_NOW
    )

    assert result.status == "created"
    row = db_session.get(DatasetVersions, result.dataset_version_id)
    assert row is not None and row.checksum
    checksum_rows = (
        db_session.query(Checksums)
        .filter(Checksums.dataset_version == result.dataset_version_id)
        .all()
    )
    assert checksum_rows and all(r.checksum_value for r in checksum_rows)


def test_registration_failure_removes_the_written_version(
    db_session: Session, base_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d1 = date(2026, 10, 22)
    seed_daily_version(db_session, base_path, day=d1, tables=[_bars("AAPL", d1, [1.0, 2.0])])
    before = {p.name for p in (base_path / "bars" / "raw").iterdir()}

    def _boom(self: object, dataset_version: object) -> None:
        raise RuntimeError("registration failed")

    monkeypatch.setattr(rolling_module.DatasetRegistrationService, "save", _boom)

    with pytest.raises(RuntimeError, match="registration failed"):
        RollingRawBarsService(db_session, base_path=base_path).build(
            as_of=d1, sessions=1, now_utc=_NOW
        )

    # No orphan directory for the publish step to ship, and nothing registered.
    assert {p.name for p in (base_path / "bars" / "raw").iterdir()} == before
    assert db_session.query(Checksums).count() == 0
    assert (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.metadata_json["dataset_lifecycle"].as_string() == ROLLING_LIFECYCLE)
        .count()
        == 0
    )
