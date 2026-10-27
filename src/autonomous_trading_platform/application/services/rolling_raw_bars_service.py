"""A rolling multi-session raw-bars dataset version for the bench window (Finding 14).

Live ingestion cuts one ``raw_bars`` version per session (``active_daily_incremental``,
coverage = that day). Re-simulations need one version spanning the bench window, which
backtests got for free from their cumulative replay dataset. This service writes that
version on Box A at end of day: the last ``sessions`` daily versions are read back,
merged (deduplicated by ``bar_id``), written as a new immutable version with its own
file checksums, and registered as ``validated`` with
``metadata_json.dataset_lifecycle = "rolling_bench_window"``. ``resolve_bench_window``
prefers the newest rolling version; the publish step ships it like any other, so the
worker pulls it unchanged.

Idempotent per set of sources: when the newest rolling version was built from the same
daily versions, nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.compute_checksum import compute_file_checksum
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root, get_data_root
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

DAILY_LIFECYCLE = "active_daily_incremental"
ROLLING_LIFECYCLE = "rolling_bench_window"
ROLLING_SOURCE = "rolling_raw_bars"

# Stored columns only; write_table re-derives the partition columns (date/year/month).
_BAR_COLUMNS = [f.name for f in BAR_SCHEMA if f.name not in ("date", "year", "month")]


@dataclass
class RollingRawBarsResult:
    status: str  # created | up_to_date | skipped
    dataset_version_id: str | None = None
    source_versions: list[str] = field(default_factory=list)
    sessions_requested: int = 0
    coverage_start: date | None = None
    coverage_end: date | None = None
    symbols: list[str] = field(default_factory=list)
    rows: int = 0
    reason: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dataset_version_id": self.dataset_version_id,
            "sessions": len(self.source_versions),
            "sessions_requested": self.sessions_requested,
            "coverage_start": self.coverage_start.isoformat() if self.coverage_start else None,
            "coverage_end": self.coverage_end.isoformat() if self.coverage_end else None,
            "symbols": len(self.symbols),
            "rows": self.rows,
            "reason": self.reason,
        }


class RollingRawBarsService:
    def __init__(
        self,
        session: Session,
        *,
        base_path: str | Path | None = None,
        reader: HistoricalBarDatasetReader | None = None,
    ) -> None:
        self._session = session
        self._base_path = Path(base_path) if base_path is not None else get_data_root()
        self._reader = reader or HistoricalBarDatasetReader(session, base_path=self._base_path)

    # ------------------------------------------------------------------ public

    def build(self, *, as_of: date, sessions: int, now_utc: datetime) -> RollingRawBarsResult:
        sessions = max(int(sessions), 1)
        sources = self.daily_versions(as_of=as_of, sessions=sessions)
        if not sources:
            return RollingRawBarsResult(
                status="skipped", sessions_requested=sessions, reason="no_daily_versions"
            )
        source_ids = [row.dataset_version_id for row in sources]

        latest = self.latest_rolling()
        if latest is not None and _source_versions(latest) == source_ids:
            return RollingRawBarsResult(
                status="up_to_date",
                dataset_version_id=latest.dataset_version_id,
                source_versions=source_ids,
                sessions_requested=sessions,
                coverage_start=latest.date_coverage_start,
                coverage_end=latest.date_coverage_end,
                symbols=_declared_symbols(latest),
                reason="same_sources",
            )

        symbols = sorted({sym for row in sources for sym in _declared_symbols(row)})
        table = self._merge(sources, symbols)
        if table.num_rows == 0:
            return RollingRawBarsResult(
                status="skipped",
                source_versions=source_ids,
                sessions_requested=sessions,
                reason="no_bars_in_sources",
            )

        version_id = generate_dataset_version("raw_bars")
        write_table(table, RAW_BARS_DATASET, self._base_path, version_id)
        checksum = self._register_checksums(version_id)

        coverage_start = min(row.date_coverage_start for row in sources if row.date_coverage_start)
        coverage_end = max(row.date_coverage_end for row in sources if row.date_coverage_end)
        DatasetRegistrationService(session=self._session).save(
            DatasetVersion(
                dataset_version_id=version_id,
                dataset_name=RAW_BARS_DATASET.dataset_key,
                created_at=now_utc,
                source=ROLLING_SOURCE,
                price_basis=PriceBasis.RAW,
                interval=BarInterval.FIVE_MIN,
                schema_version=RAW_BARS_DATASET.schema_version,
                symbol_coverage=len(symbols),
                date_coverage_start=coverage_start,
                date_coverage_end=coverage_end,
                validation_status="validated",
                checksum=checksum,
                source_manifest={
                    "pipeline": ROLLING_SOURCE,
                    "symbols": symbols,
                    "source_versions": source_ids,
                    "sessions": len(source_ids),
                    "as_of": as_of.isoformat(),
                },
                metadata_json={
                    "dataset_lifecycle": ROLLING_LIFECYCLE,
                    "source_versions": source_ids,
                    "sessions": len(source_ids),
                    "as_of": as_of.isoformat(),
                },
            )
        )
        self._session.flush()
        result = RollingRawBarsResult(
            status="created",
            dataset_version_id=version_id,
            source_versions=source_ids,
            sessions_requested=sessions,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            symbols=symbols,
            rows=table.num_rows,
        )
        logger.info("rolling_raw_bars.created", extra=result.summary())
        return result

    def daily_versions(self, *, as_of: date, sessions: int) -> list[DatasetVersions]:
        """The newest ``sessions`` validated per-session versions ending on or before as_of,
        oldest first."""
        rows = (
            self._session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(DatasetVersions.date_coverage_end <= as_of)
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string() == DAILY_LIFECYCLE
            )
            .order_by(DatasetVersions.date_coverage_end.desc(), DatasetVersions.created_at.desc())
            .all()
        )
        # One version per session: the newest registration for a day wins.
        by_day: dict[date, DatasetVersions] = {}
        for row in rows:
            day = row.date_coverage_end
            if day is not None and day not in by_day:
                by_day[day] = row
        newest = sorted(by_day.values(), key=lambda r: r.date_coverage_end or date.min)
        return newest[-sessions:]

    def latest_rolling(self) -> DatasetVersions | None:
        row = (
            self._session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == RAW_BARS_DATASET.dataset_key)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string() == ROLLING_LIFECYCLE
            )
            .order_by(DatasetVersions.date_coverage_end.desc(), DatasetVersions.created_at.desc())
            .first()
        )
        return row if isinstance(row, DatasetVersions) else None

    # ----------------------------------------------------------------- private

    def _merge(self, sources: list[DatasetVersions], symbols: list[str]) -> pa.Table:
        tables: list[pa.Table] = []
        for row in sources:
            start, end = row.date_coverage_start, row.date_coverage_end
            if start is None or end is None:
                continue
            for symbol in _declared_symbols(row) or symbols:
                table = self._reader.read(
                    dataset=RAW_BARS_DATASET,
                    dataset_version=row.dataset_version_id,
                    symbol=symbol,
                    start_date=start,
                    end_date=end,
                )
                if table.num_rows:
                    tables.append(table.select(_BAR_COLUMNS))
        if not tables:
            return BAR_SCHEMA.empty_table()
        combined = pa.concat_tables(tables, promote_options="default")
        combined = _dedupe_by_bar_id(combined)
        return combined.sort_by([("symbol", "ascending"), ("timestamp", "ascending")])

    def _register_checksums(self, version_id: str) -> str:
        root = dataset_version_root(self._base_path, RAW_BARS_DATASET, version_id)
        files = sorted(p for p in root.rglob("*.parquet") if p.is_file())
        import hashlib

        aggregate = hashlib.sha256()
        with SorUnitOfWork(self._session) as uow:
            for path in files:
                value = compute_file_checksum(path)
                uow.checksums.upsert(
                    uow.checksums.build_row(
                        dataset_version=version_id,
                        object_type="parquet_file",
                        object_path=str(path),
                        checksum_algorithm="sha256",
                        checksum_value=value,
                    )
                )
                aggregate.update(f"{path}|{value}\n".encode())
        return aggregate.hexdigest()


def _declared_symbols(row: DatasetVersions) -> list[str]:
    manifest = row.source_manifest or {}
    symbols = manifest.get("symbols")
    if isinstance(symbols, list):
        return sorted(str(s) for s in symbols if s)
    return []


def _source_versions(row: DatasetVersions) -> list[str]:
    meta = row.metadata_json or {}
    versions = meta.get("source_versions")
    return [str(v) for v in versions] if isinstance(versions, list) else []


def _dedupe_by_bar_id(table: pa.Table) -> pa.Table:
    """Keep the last occurrence per bar_id, as the daily chunk writer does."""
    seen: dict[str, int] = {}
    for index, bar_id in enumerate(table.column("bar_id").to_pylist()):
        seen[bar_id] = index
    if len(seen) == table.num_rows:
        return table
    return table.take(pa.array(sorted(seen.values())))


def utc_now() -> datetime:
    return datetime.now(UTC)
