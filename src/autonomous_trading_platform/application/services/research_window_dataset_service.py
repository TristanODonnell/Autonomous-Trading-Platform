"""One checked raw-bars version over a long research window.

A window of a year or more is fetched as monthly ``atp ingestion run-backfill`` versions.
The research engine reads one dataset version, so this service merges those versions into
one, clipped to the window and to the requested symbols, and looks at the result before
registering it:

- A session with no bars for any symbol, or with a number of distinct bar times other
  than its opening hours give, is a fault in the dataset. The build is refused and
  nothing is written.
- A symbol with no bars on a session, and a close far from the previous close, are
  reported. Thin names and splits make both normal in raw prices.

The version is tagged ``dataset_lifecycle = "research_window"``. The bench window looks
for ``rolling_bench_window`` versions, so it never picks this one up.

It extends ``RollingRawBarsService`` to share the reading, writing and registering of a
merged version. What differs is which versions are merged and what is checked.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pyarrow as pa

from autonomous_trading_platform.application.services.rolling_raw_bars_service import (
    _BAR_COLUMNS,
    BACKFILL_DATASET_TYPE,
    RollingRawBarsService,
    _declared_symbols,
    _dedupe_by_bar_id,
    _source_versions,
)
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    RESEARCH_WINDOW_LIFECYCLE,
    RESEARCH_WINDOW_SOURCE,
    PriceJump,
    ResearchWindowDatasetResult,
    SessionShape,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.runtime.clock import MarketCalendar, RealMarketCalendar
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

logger = get_logger(__name__)

# A close this far from the previous close of the same symbol is listed in the result.
PRICE_JUMP_THRESHOLD = 0.25
_BAR_MINUTES = 5
_SESSION_ZONE = "America/New_York"


class ResearchWindowDatasetService(RollingRawBarsService):
    def build_window(
        self,
        *,
        start: date,
        end: date,
        symbols: Iterable[str],
        now_utc: datetime,
        calendar: MarketCalendar | None = None,
        plan_only: bool = False,
    ) -> ResearchWindowDatasetResult:
        names = sorted({s.strip().upper() for s in symbols if s and s.strip()})
        if not names:
            raise ValueError("at least one symbol is required")
        if end < start:
            raise ValueError(f"--end {end} is before --start {start}")
        market = calendar or RealMarketCalendar()
        sessions = list(market.trading_days(start, end))
        if not sessions:
            raise ValueError(f"no trading session between {start} and {end}")

        sources = self.window_sources(start=start, end=end, symbols=names)
        source_ids = [row.dataset_version_id for row in sources]
        base: dict[str, Any] = {
            "start": start,
            "end": end,
            "symbols": names,
            "source_versions": source_ids,
            "sessions_expected": len(sessions),
        }

        uncovered = [day for day in sessions if not _covered(day, sources)]
        if uncovered:
            return ResearchWindowDatasetResult(
                status="refused",
                sessions_missing=uncovered,
                errors=[
                    f"{len(uncovered)} session(s) are in no backfill version that holds "
                    f"every requested symbol, the first is {uncovered[0]}"
                ],
                **base,
            )

        existing = self.latest_window(start=start, end=end, symbols=names, sources=source_ids)
        if existing is not None:
            return ResearchWindowDatasetResult(
                status="up_to_date", dataset_version_id=existing.dataset_version_id, **base
            )
        if plan_only:
            return ResearchWindowDatasetResult(status="planned", **base)

        table = self._merge_window(sources, names, start=start, end=end)
        checks = _check(table, names, sessions, market)
        found: dict[str, Any] = {**base, "rows": table.num_rows, **checks}
        errors = _faults(checks)
        if errors:
            return ResearchWindowDatasetResult(status="refused", errors=errors, **found)

        days = table.column("timestamp").to_pandas().dt.tz_convert(_SESSION_ZONE).dt.date
        window_facts = {
            "source_versions": source_ids,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
        version_id = self._write_and_register(
            table,
            symbols=names,
            coverage_start=min(days),
            coverage_end=max(days),
            now_utc=now_utc,
            source=RESEARCH_WINDOW_SOURCE,
            source_manifest={"pipeline": RESEARCH_WINDOW_SOURCE, "symbols": names, **window_facts},
            metadata_json={
                "dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE,
                **window_facts,
                "sessions": len(sessions),
                "price_jumps": len(checks["price_jumps"]),
            },
        )
        result = ResearchWindowDatasetResult(
            status="created", dataset_version_id=version_id, **found
        )
        logger.info(
            "research_window_dataset.created",
            extra={
                "dataset_version_id": version_id,
                "rows": table.num_rows,
                "symbols": len(names),
                "sessions": len(sessions),
                "price_jumps": len(result.price_jumps),
            },
        )
        return result

    def window_sources(
        self, *, start: date, end: date, symbols: list[str]
    ) -> list[DatasetVersions]:
        """Validated backfill versions that overlap the window and hold every requested
        symbol, oldest first.

        A version fetched for another list of names is left out: merging it would give
        some names more history than others. Overlapping versions are fine, the merge
        keeps one bar per ``bar_id`` and the later version wins.
        """
        rows = (
            self._session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(
                DatasetVersions.metadata_json["dataset_type"].as_string() == BACKFILL_DATASET_TYPE
            )
            .filter(DatasetVersions.date_coverage_end >= start)
            .filter(DatasetVersions.date_coverage_start <= end)
            .order_by(DatasetVersions.date_coverage_start.asc(), DatasetVersions.created_at.asc())
            .all()
        )
        wanted = set(symbols)
        return [
            row
            for row in rows
            if isinstance(row, DatasetVersions) and wanted <= set(_declared_symbols(row))
        ]

    def latest_window(
        self, *, start: date, end: date, symbols: list[str], sources: list[str]
    ) -> DatasetVersions | None:
        """The newest research-window version built from exactly these inputs, if any."""
        rows = (
            self._session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string()
                == RESEARCH_WINDOW_LIFECYCLE
            )
            .order_by(DatasetVersions.created_at.desc())
            .all()
        )
        for row in rows:
            if not isinstance(row, DatasetVersions):
                continue
            meta = row.metadata_json or {}
            if (
                meta.get("window_start") == start.isoformat()
                and meta.get("window_end") == end.isoformat()
                and _declared_symbols(row) == symbols
                and _source_versions(row) == sources
            ):
                return row
        return None

    def _merge_window(
        self, sources: list[DatasetVersions], symbols: list[str], *, start: date, end: date
    ) -> pa.Table:
        tables: list[pa.Table] = []
        for row in sources:
            if row.date_coverage_start is None or row.date_coverage_end is None:
                continue
            first = max(start, row.date_coverage_start)
            last = min(end, row.date_coverage_end)
            if last < first:
                continue
            for symbol in symbols:
                table = self._reader.read(
                    dataset=RAW_BARS_DATASET,
                    dataset_version=row.dataset_version_id,
                    symbol=symbol,
                    start_date=first,
                    end_date=last,
                )
                if table.num_rows:
                    tables.append(table.select(_BAR_COLUMNS))
        if not tables:
            return BAR_SCHEMA.empty_table().select(_BAR_COLUMNS)
        combined = _dedupe_by_bar_id(pa.concat_tables(tables, promote_options="default"))
        return combined.sort_by([("symbol", "ascending"), ("timestamp", "ascending")])


def _covered(day: date, sources: list[DatasetVersions]) -> bool:
    return any(
        row.date_coverage_start is not None
        and row.date_coverage_end is not None
        and row.date_coverage_start <= day <= row.date_coverage_end
        for row in sources
    )


def _expected_bar_times(day: date, calendar: MarketCalendar) -> int:
    opened, closed = calendar.session_times(day)
    return int((closed - opened) / timedelta(minutes=_BAR_MINUTES))


def _check(
    table: pa.Table, symbols: list[str], sessions: list[date], calendar: MarketCalendar
) -> dict[str, Any]:
    """What the merged bars look like, as fields of the result."""
    expected = {day: _expected_bar_times(day, calendar) for day in sessions}
    frame = table.select(["symbol", "timestamp", "close"]).to_pandas()
    frame["session"] = frame["timestamp"].dt.tz_convert(_SESSION_ZONE).dt.date

    bar_times = frame.groupby("session")["timestamp"].nunique().to_dict()
    missing = [day for day in sessions if day not in bar_times]
    off_shape = [
        SessionShape(session=day, bar_times=int(count), expected=expected.get(day, 0))
        for day, count in sorted(bar_times.items())
        if int(count) != expected.get(day, 0)
    ]

    slots = sum(expected.values())
    bars = frame.groupby("symbol").size().to_dict()
    present = frame.groupby("symbol")["session"].nunique().to_dict()
    coverage = {
        symbol: round(int(bars.get(symbol, 0)) / slots, 4) if slots else 0.0 for symbol in symbols
    }
    sessions_missing = {
        symbol: len(sessions) - int(present.get(symbol, 0))
        for symbol in symbols
        if int(present.get(symbol, 0)) < len(sessions)
    }

    frame = frame.sort_values(["symbol", "timestamp"], kind="stable")
    previous = frame.groupby("symbol")["close"].shift(1)
    change = frame["close"] / previous - 1.0
    jumped = frame[change.abs() > PRICE_JUMP_THRESHOLD]
    jumps = [
        PriceJump(
            symbol=str(row.symbol),
            timestamp=pd.Timestamp(row.timestamp).to_pydatetime(),
            previous_close=float(previous.loc[index]),
            close=float(row.close),
            change=round(float(change.loc[index]), 4),
        )
        for index, row in jumped.iterrows()
    ]
    jumps.sort(key=lambda jump: (jump.timestamp, jump.symbol))
    return {
        "sessions_missing": missing,
        "sessions_off_shape": off_shape,
        "symbol_coverage": coverage,
        "symbol_sessions_missing": sessions_missing,
        "price_jumps": jumps,
    }


def _faults(checks: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing: list[date] = checks["sessions_missing"]
    off_shape: list[SessionShape] = checks["sessions_off_shape"]
    if missing:
        errors.append(
            f"{len(missing)} session(s) have no bars for any symbol, the first is {missing[0]}"
        )
    if off_shape:
        first = off_shape[0]
        errors.append(
            f"{len(off_shape)} session(s) do not have the number of bar times their opening "
            f"hours give, the first is {first.session} ({first.bar_times} of {first.expected})"
        )
    return errors
