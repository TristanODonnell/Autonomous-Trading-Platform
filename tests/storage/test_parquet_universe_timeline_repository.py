"""ParquetUniverseTimelineRepository: one timeline version read back as its lists."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.storage.parquet.datasets import UNIVERSE_TIMELINE_DATASET
from autonomous_trading_platform.storage.parquet.repositories.parquet_universe_timeline_repository import (
    ParquetUniverseTimelineRepository,
)
from autonomous_trading_platform.storage.parquet.schemas import UNIVERSE_TIMELINE_SCHEMA
from autonomous_trading_platform.storage.parquet.writer import write_table

_T_START = datetime(2024, 11, 18, tzinfo=UTC)
_T_DEC = datetime(2024, 12, 31, 23, tzinfo=UTC)
Row = tuple[str, datetime, datetime | None, str, int | None]


def _table(rows: list[Row]) -> pa.Table:
    columns: dict[str, list[Any]] = {name: [] for name in UNIVERSE_TIMELINE_SCHEMA.names}
    for row in rows:
        for name, value in zip(UNIVERSE_TIMELINE_SCHEMA.names, row, strict=True):
            columns[name].append(value)
    return pa.table(columns, schema=UNIVERSE_TIMELINE_SCHEMA)


_ROWS: list[Row] = [
    # Written out of order on purpose: the reader sorts lists by date, members by rank.
    ("v2", _T_DEC, None, "DDD", 4),
    ("v2", _T_DEC, None, "AAA", 2),
    ("v1", _T_START, _T_DEC, "CCC", None),
    ("v1", _T_START, _T_DEC, "BBB", 7),
    ("v1", _T_START, _T_DEC, "AAA", 1),
]


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


def test_a_version_reads_back_as_its_lists_oldest_first(base_path: Path) -> None:
    write_table(_table(_ROWS), UNIVERSE_TIMELINE_DATASET, base_path, "tl-1")

    periods = ParquetUniverseTimelineRepository(base_path=base_path).read_periods("tl-1")

    assert [(p.universe_version_id, p.effective_from, p.effective_to) for p in periods] == [
        ("v1", _T_START, _T_DEC),
        ("v2", _T_DEC, None),
    ]
    # Best rank first; a name with no rank comes last.
    assert [(m.symbol, m.rank) for m in periods[0].members] == [
        ("AAA", 1),
        ("BBB", 7),
        ("CCC", None),
    ]
    assert [(m.symbol, m.rank) for m in periods[1].members] == [("AAA", 2), ("DDD", 4)]


def test_a_version_is_one_file_in_its_own_folder(base_path: Path) -> None:
    write_table(_table(_ROWS), UNIVERSE_TIMELINE_DATASET, base_path, "tl-1")

    root = ParquetUniverseTimelineRepository(base_path=base_path).version_root("tl-1")

    assert root == base_path / "universe_timeline" / "dataset_version=tl-1"
    assert len(list(root.rglob("*.parquet"))) == 1
    assert [p.name for p in root.iterdir() if p.is_dir()] == []


def test_a_version_that_is_not_on_disk_is_an_error(base_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="tl-missing has no file"):
        ParquetUniverseTimelineRepository(base_path=base_path).read_periods("tl-missing")


def test_one_list_stored_with_two_spans_is_an_error(base_path: Path) -> None:
    rows: list[Row] = [*_ROWS, ("v1", _T_START, None, "EEE", 9)]
    write_table(_table(rows), UNIVERSE_TIMELINE_DATASET, base_path, "tl-1")

    with pytest.raises(ValueError, match="list v1 is stored with two spans"):
        ParquetUniverseTimelineRepository(base_path=base_path).read_periods("tl-1")


def test_a_file_without_the_timeline_columns_is_an_error(base_path: Path) -> None:
    repository = ParquetUniverseTimelineRepository(base_path=base_path)
    root = repository.version_root("tl-1")
    root.mkdir(parents=True)
    pq.write_table(pa.table({"symbol": ["AAA"]}), root / "part-0.parquet")

    with pytest.raises(ValueError, match="lacks column"):
        repository.read_periods("tl-1")
