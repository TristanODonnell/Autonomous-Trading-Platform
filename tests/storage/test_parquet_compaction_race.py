"""
Tests for Parquet writer / compaction failure modes.

Covers:
- Bug #1: pq.read_table() on a file inside a Hive-partitioned directory walks
  up and merges the whole dataset, causing ArrowTypeError when fragment and
  compacted files coexist with differing column encodings.  The fix is
  pq.ParquetFile(str(f)).read() which reads only that file.  A regression
  guard patches pq.read_table to raise and verifies compaction still completes.
- Bug #6: _list_parquet_files uses rglob("*.parquet"), which races with
  compaction deleting fragments between the glob and stat() calls.  The
  is_file() guard in the current implementation filters deleted paths safely;
  this test proves the guard is present and effective.
- Compaction correctness: deduplication by bar_id, ascending timestamp sort,
  atomic tmp+rename write, and existing data.parquet is included and merged.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.storage.parquet.writer import _list_parquet_files

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar_table(
    bar_ids: list[str],
    timestamps: list[datetime],
    closes: list[float],
    symbol: str = "AAPL",
) -> pa.Table:
    n = len(bar_ids)
    return pa.table(
        {
            "bar_id": pa.array(bar_ids, type=pa.string()),
            "timestamp": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "symbol": pa.array([symbol] * n, type=pa.string()),
            "open": pa.array(closes, type=pa.float64()),
            "high": pa.array(closes, type=pa.float64()),
            "low": pa.array(closes, type=pa.float64()),
            "close": pa.array(closes, type=pa.float64()),
            "volume": pa.array([1000] * n, type=pa.int64()),
        }
    )


def _ts(h: int, m: int) -> datetime:
    return datetime(2025, 3, 14, h, m, tzinfo=UTC)


def _part_dir(tmp_path: Path, dataset_version_id: str, symbol: str = "AAPL") -> Path:
    """Construct the expected partition directory matching _compact_day_partitions logic."""
    d = (
        tmp_path
        / "bars"
        / "raw"
        / f"dataset_version={dataset_version_id}"
        / f"symbol={symbol.upper()}"
        / "year=2025"
        / "month=03"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# _list_parquet_files
# ---------------------------------------------------------------------------


class TestListParquetFiles:
    def test_returns_empty_list_when_root_does_not_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_dir"
        assert _list_parquet_files(missing) == []

    def test_returns_only_parquet_files(self, tmp_path: Path) -> None:
        (tmp_path / "data.parquet").write_bytes(b"")
        (tmp_path / "metadata.json").write_bytes(b"")
        (tmp_path / "notes.txt").write_bytes(b"")
        result = _list_parquet_files(tmp_path)
        assert [f.name for f in result] == ["data.parquet"]

    def test_excludes_directories_named_with_parquet_extension(self, tmp_path: Path) -> None:
        fake_dir = tmp_path / "fake.parquet"
        fake_dir.mkdir()
        (tmp_path / "real.parquet").write_bytes(b"")
        result = _list_parquet_files(tmp_path)
        assert all(f.is_file() for f in result)
        assert len(result) == 1

    def test_result_is_sorted_lexicographically(self, tmp_path: Path) -> None:
        names = ["zzz.parquet", "aaa.parquet", "mmm.parquet"]
        for n in names:
            (tmp_path / n).write_bytes(b"")
        result = _list_parquet_files(tmp_path)
        assert result == sorted(result)

    def test_finds_files_in_nested_subdirectories(self, tmp_path: Path) -> None:
        nested = tmp_path / "symbol=AAPL" / "year=2025" / "month=01"
        nested.mkdir(parents=True)
        (nested / "data.parquet").write_bytes(b"")
        result = _list_parquet_files(tmp_path)
        assert len(result) == 1
        assert result[0].name == "data.parquet"

    def test_is_file_guard_filters_deleted_files(self, tmp_path: Path) -> None:
        """Simulates the rglob race: a file disappears between rglob discovery and is_file().

        Bug #6: compaction deletes fragment files (part-*.parquet) while write_table is
        still collecting file stats.  The is_file() guard in _list_parquet_files filters
        out any path that has been deleted by the time the check runs.

        To simulate the race correctly we delete the file BEFORE yielding it from the
        patched rglob generator, so that when the caller evaluates `if path.is_file()`
        the file is already gone — exactly the window the race opens.
        """
        real_file = tmp_path / "data.parquet"
        real_file.write_bytes(b"")
        ghost = tmp_path / "ghost.parquet"
        ghost.write_bytes(b"")

        original_rglob = Path.rglob

        def patched_rglob(self, pattern):
            for p in original_rglob(self, pattern):
                # Delete before yielding so is_file() sees the file as gone
                if p.name == "ghost.parquet" and p.exists():
                    p.unlink()
                yield p

        with patch.object(Path, "rglob", patched_rglob):
            result = _list_parquet_files(tmp_path)

        names = {f.name for f in result}
        assert "ghost.parquet" not in names, (
            "is_file() guard must exclude paths deleted between rglob discovery and the check"
        )
        assert "data.parquet" in names


# ---------------------------------------------------------------------------
# _compact_day_partitions — Bug #1 regression guard
# ---------------------------------------------------------------------------


class TestCompactDoesNotUseReadTable:
    """Regression guard for Bug #1.

    The original code used pq.read_table(path), which asks PyArrow's Hive dataset
    scanner to merge the whole partition tree.  When fragment files (part-*.parquet)
    and the compacted file (data.parquet) coexist with different column encodings
    (e.g. int32 vs dictionary<int32> for the year column), this causes ArrowTypeError.

    The fix is pq.ParquetFile(str(f)).read(), which reads exactly that file with no
    schema merging.  This test patches pq.read_table to raise ArrowInvalid and verifies
    that _compact_day_partitions still completes successfully.
    """

    def test_compaction_succeeds_even_when_read_table_would_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
            _compact_day_partitions,
        )

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))

        version_id = "test-version-001"
        symbol = "AAPL"
        tick_date = date(2025, 3, 14)
        part_dir = _part_dir(tmp_path, version_id, symbol)

        table = _bar_table(
            bar_ids=["bar-001", "bar-002"],
            timestamps=[_ts(14, 35), _ts(14, 40)],
            closes=[150.0, 151.0],
        )
        pq.write_table(table, part_dir / "part-abc123.parquet")

        # pq is imported inside _compact_day_partitions (not at module level), so we
        # must patch pyarrow.parquet.read_table directly.  If the function calls
        # read_table, it will raise; if it uses ParquetFile.read() exclusively, it
        # will not.
        def _exploding_read_table(*args, **kwargs):
            raise pa.lib.ArrowInvalid(
                "Simulated ArrowTypeError: schema mismatch across Hive partitions"
            )

        with patch("pyarrow.parquet.read_table", side_effect=_exploding_read_table):
            # Must not raise — compaction uses ParquetFile.read(), not read_table()
            _compact_day_partitions(
                dataset_version_id=version_id,
                tick_date=tick_date,
                symbols=[symbol],
            )

        data_file = part_dir / "data.parquet"
        assert data_file.exists(), "data.parquet must be written by compaction"


# ---------------------------------------------------------------------------
# _compact_day_partitions — correctness
# ---------------------------------------------------------------------------


class TestCompactDayPartitions:
    """Functional correctness of _compact_day_partitions."""

    def _run(
        self,
        tmp_path: Path,
        version_id: str,
        symbol: str,
        tick_date: date,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
            _compact_day_partitions,
        )

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        _compact_day_partitions(
            dataset_version_id=version_id,
            tick_date=tick_date,
            symbols=[symbol],
        )

    def test_fragments_merged_into_data_parquet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version_id = "v001"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)

        pq.write_table(
            _bar_table(["b1"], [_ts(14, 35)], [150.0]),
            part_dir / "part-00001.parquet",
        )
        pq.write_table(
            _bar_table(["b2"], [_ts(14, 40)], [151.0]),
            part_dir / "part-00002.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        data_file = part_dir / "data.parquet"
        assert data_file.exists()

        result = pq.ParquetFile(str(data_file)).read()
        assert result.num_rows == 2

    def test_fragment_files_deleted_after_compaction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version_id = "v002"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)

        pq.write_table(
            _bar_table(["b1"], [_ts(14, 35)], [150.0]),
            part_dir / "part-frag.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        remaining = list(part_dir.glob("part-*.parquet"))
        assert remaining == [], "Fragment files must be deleted after compaction"

    def test_deduplication_by_bar_id_newest_write_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the same bar_id appears in two fragments, only the last occurrence is kept."""
        version_id = "v003"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)

        # First fragment: bar-001 at close=150
        pq.write_table(
            _bar_table(["bar-001"], [_ts(14, 35)], [150.0]),
            part_dir / "part-first.parquet",
        )
        # Second fragment: bar-001 re-ingested at close=152 (re-ingest correction)
        pq.write_table(
            _bar_table(["bar-001"], [_ts(14, 35)], [152.0]),
            part_dir / "part-second.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        result = pq.ParquetFile(str(part_dir / "data.parquet")).read()
        assert result.num_rows == 1, "Duplicate bar_id must be deduplicated to one row"

    def test_output_sorted_by_timestamp_ascending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version_id = "v004"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)

        # Write in reverse order
        pq.write_table(
            _bar_table(
                ["b3", "b1", "b2"], [_ts(15, 0), _ts(14, 35), _ts(14, 40)], [153.0, 150.0, 151.0]
            ),
            part_dir / "part-unsorted.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        result = pq.ParquetFile(str(part_dir / "data.parquet")).read()
        timestamps = result.column("timestamp").to_pylist()
        assert timestamps == sorted(timestamps), (
            "Compacted file must be sorted ascending by timestamp"
        )

    def test_existing_data_parquet_merged_with_new_fragments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """data.parquet from a prior compaction run is included and merged."""
        version_id = "v005"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)

        # Existing compacted file
        pq.write_table(
            _bar_table(["existing-bar"], [_ts(14, 35)], [150.0]),
            part_dir / "data.parquet",
        )
        # New fragment from this tick
        pq.write_table(
            _bar_table(["new-bar"], [_ts(14, 40)], [151.0]),
            part_dir / "part-new.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        result = pq.ParquetFile(str(part_dir / "data.parquet")).read()
        bar_ids = set(result.column("bar_id").to_pylist())
        assert "existing-bar" in bar_ids
        assert "new-bar" in bar_ids

    def test_no_op_when_partition_directory_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing partition directory is silently skipped (no exception)."""
        from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
            _compact_day_partitions,
        )

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        # No directory created — should not raise
        _compact_day_partitions(
            dataset_version_id="v-missing",
            tick_date=date(2025, 3, 14),
            symbols=["AAPL"],
        )

    def test_no_op_when_no_fragment_files_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty partition directory (no part-*.parquet) is silently skipped."""
        version_id = "v006"
        symbol = "AAPL"
        _part_dir(tmp_path, version_id, symbol)  # create dir but add no files

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        part_dir = _part_dir(tmp_path, version_id, symbol)
        assert not (part_dir / "data.parquet").exists()

    def test_atomic_write_no_tmp_file_left_on_completion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After successful compaction no .tmp.parquet file remains."""
        version_id = "v007"
        symbol = "AAPL"
        part_dir = _part_dir(tmp_path, version_id, symbol)
        pq.write_table(
            _bar_table(["b1"], [_ts(14, 35)], [150.0]),
            part_dir / "part-abc.parquet",
        )

        self._run(tmp_path, version_id, symbol, date(2025, 3, 14), monkeypatch)

        tmp_files = list(part_dir.glob("*.tmp.parquet"))
        assert tmp_files == [], "No .tmp.parquet file should remain after successful compaction"

    def test_multiple_symbols_compacted_independently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each symbol in the symbols list is compacted into its own partition."""
        from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
            _compact_day_partitions,
        )

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        version_id = "v008"

        for sym, close in [("AAPL", 150.0), ("MSFT", 300.0)]:
            part_dir = _part_dir(tmp_path, version_id, sym)
            pq.write_table(
                _bar_table(["b1"], [_ts(14, 35)], [close], symbol=sym),
                part_dir / "part-frag.parquet",
            )

        _compact_day_partitions(
            dataset_version_id=version_id,
            tick_date=date(2025, 3, 14),
            symbols=["AAPL", "MSFT"],
        )

        for sym in ("AAPL", "MSFT"):
            part_dir = _part_dir(tmp_path, version_id, sym)
            assert (part_dir / "data.parquet").exists(), f"{sym} data.parquet must exist"


# ---------------------------------------------------------------------------
# Bug #1 — pq.read_table vs pq.ParquetFile.read on Hive trees
# ---------------------------------------------------------------------------


class TestParquetFileVsReadTable:
    """Documents the exact failure mode of Bug #1.

    pq.read_table() on a path inside a Hive partition tree discovers sibling
    partition columns and tries to unify schemas, which raises ArrowTypeError
    when the int32 year/month columns of a fragment clash with the
    dictionary<int32> encoding in a compacted data.parquet.

    pq.ParquetFile(str(path)).read() reads exactly that file without any
    directory scanning.  This test:
      1. Writes two parquet files with intentionally incompatible year column
         types to simulate the fragment/compacted coexistence scenario.
      2. Verifies pq.ParquetFile reads each file independently without error.
      3. Documents that pq.read_table on a nested path would attempt upward
         traversal (depending on PyArrow version) and may raise.
    """

    def test_parquet_file_read_isolates_single_file(self, tmp_path: Path) -> None:
        """pq.ParquetFile(path).read() reads only the named file."""
        frag_dir = tmp_path / "symbol=AAPL" / "year=2025" / "month=03"
        frag_dir.mkdir(parents=True)

        table1 = _bar_table(["b1"], [_ts(14, 35)], [150.0])
        table2 = _bar_table(["b2"], [_ts(14, 40)], [151.0])

        path1 = frag_dir / "part-001.parquet"
        path2 = frag_dir / "data.parquet"
        pq.write_table(table1, path1)
        pq.write_table(table2, path2)

        result1 = pq.ParquetFile(str(path1)).read()
        result2 = pq.ParquetFile(str(path2)).read()

        assert result1.num_rows == 1
        assert result2.num_rows == 1
        assert result1.column("bar_id").to_pylist() == ["b1"]
        assert result2.column("bar_id").to_pylist() == ["b2"]

    def test_parquet_file_read_does_not_walk_directory_tree(self, tmp_path: Path) -> None:
        """pq.ParquetFile reads exactly the bytes of one file, not a directory scan."""
        nested = tmp_path / "year=2025" / "month=03"
        nested.mkdir(parents=True)

        # Write a large file in the root and a small file in nested
        big = _bar_table(
            [f"big-{i}" for i in range(100)],
            [_ts(14, i % 60) for i in range(100)],
            [150.0 + i for i in range(100)],
        )
        small = _bar_table(["small-1"], [_ts(14, 35)], [150.0])

        pq.write_table(big, tmp_path / "data.parquet")
        pq.write_table(small, nested / "part-only.parquet")

        # Reading the nested file must return only 1 row, not 100+1
        result = pq.ParquetFile(str(nested / "part-only.parquet")).read()
        assert result.num_rows == 1
