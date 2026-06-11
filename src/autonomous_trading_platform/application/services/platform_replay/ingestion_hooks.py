"""Ingestion domain replay hook."""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from datetime import time as dt_time
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    IngestionReplayResult,
    IngestionSummary,
    PlatformReplayContext,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.missing_bar_incidents_repository import (
    MissingBarIncidentsRepository,
)

_MARKET_OPEN_UTC = dt_time(14, 30)  # 09:30 ET = 14:30 UTC (EST, no DST adjustment)
_MARKET_CLOSE_UTC = dt_time(21, 0)  # 16:00 ET = 21:00 UTC


def _compact_day_partitions(
    dataset_version_id: str,
    tick_date: date,
    symbols: list[str],
) -> None:
    """Merge all per-bar part-*.parquet fragments into a single data.parquet
    per symbol-month partition after each day's ingestion.

    The ingest writer creates one UUID-named file per 5-min cycle call
    (78 files/day per symbol). Compacting into one file per month partition
    keeps subsequent reads O(1) regardless of how many days have been ingested,
    and allows the universe candidate scorer to read bar history efficiently.
    Only called in backtest mode (when dataset_version_id_override is set).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
    from autonomous_trading_platform.storage.parquet.paths import get_data_root, partition_path

    base_path = get_data_root()
    year = f"{tick_date.year:04d}"
    month = f"{tick_date.month:02d}"

    for symbol in symbols:
        part_dir = partition_path(
            base_path=base_path,
            dataset=RAW_BARS_DATASET,
            dataset_version=dataset_version_id,
            partitions={"symbol": symbol.upper(), "year": year, "month": month},
        )
        if not part_dir.exists():
            continue

        fragment_files = sorted(part_dir.glob("part-*.parquet"))
        if not fragment_files:
            continue

        data_file = part_dir / "data.parquet"

        tables: list[pa.Table] = []
        if data_file.exists():
            with contextlib.suppress(Exception):
                tables.append(pq.read_table(data_file))
        for f in fragment_files:
            with contextlib.suppress(Exception):
                tables.append(pq.read_table(f))

        if not tables:
            continue

        combined = pa.concat_tables(tables)

        # Deduplicate by bar_id — newest write wins on re-ingest
        bar_ids = combined.column("bar_id").to_pylist()
        seen: dict[str, int] = {}
        for i, bid in enumerate(bar_ids):
            seen[bid] = i
        keep_indices = sorted(seen.values())
        table = combined.take(keep_indices).sort_by([("timestamp", "ascending")])

        # Atomic write: temp + rename so a crash mid-write never corrupts data_file
        tmp = data_file.with_suffix(".tmp.parquet")
        pq.write_table(table, tmp)
        tmp.replace(data_file)

        for f in fragment_files:
            with contextlib.suppress(Exception):
                f.unlink()


def run_ingestion_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
    full_day: bool = False,
    backtest_dataset_version_id: str | None = None,
) -> IngestionReplayResult:
    """Run market ingestion cycle for the given timestamp.

    Wraps run_market_ingestion_cycle so the platform runner never calls
    CLI commands directly.
    """
    base: dict[str, Any] = {
        "domain": "ingestion",
        "timestamp": timestamp,
        "run_id": str(replay_context.run_id),
    }

    if dry_run or replay_context.dry_run:
        return IngestionReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    warnings: list[str] = []

    cycle_start_override: datetime | None = None
    cycle_end_override: datetime | None = None
    if full_day:
        tick_date = timestamp.date()
        cycle_start_override = datetime.combine(tick_date, _MARKET_OPEN_UTC).replace(tzinfo=UTC)
        cycle_end_override = datetime.combine(tick_date, _MARKET_CLOSE_UTC).replace(tzinfo=UTC)

    try:
        summary = run_market_ingestion_cycle(
            now_utc=timestamp,
            symbols_override=replay_context.symbols if replay_context.symbols else None,
            enforce_lateness=False,  # historical replay — bars for past dates are never "late"
            trigger_type="platform_replay",
            actor=replay_context.actor,
            cycle_start_override=cycle_start_override,
            cycle_end_override=cycle_end_override,
            dataset_version_id_override=backtest_dataset_version_id,
        )
    except Exception as exc:
        return IngestionReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    dataset_version_id: str | None = summary.get("dataset_version_id") or None  # type: ignore[assignment]
    raw_row_count = summary.get("row_count")
    if raw_row_count is None:
        rows_written = 0
    elif isinstance(raw_row_count, int):
        rows_written = raw_row_count
    elif isinstance(raw_row_count, str):
        rows_written = int(raw_row_count)
    else:
        rows_written = 0

    # Count incidents for this run
    incident_count = 0
    try:
        if dataset_version_id:
            incidents = MissingBarIncidentsRepository(session).list_by_dataset_version(
                dataset_version=dataset_version_id
            )
            incident_count = len(incidents)
            if incident_count:
                warnings.append(f"{incident_count} missing/late bar incidents recorded")
    except Exception:
        pass

    result_summary = {
        "dataset_version_id": dataset_version_id,
        "rows_written": rows_written,
        "missing_bar_incident_count": incident_count,
        "timestamp": timestamp.isoformat(),
        **{k: v for k, v in summary.items() if k not in ("dataset_version_id", "row_count")},
    }

    # Compact fragment files into one data.parquet per symbol-month.
    # Only in backtest mode (dataset_version_id_override set) — live/paper
    # bars are written in real-time and compacted by a separate nightly job.
    if backtest_dataset_version_id and replay_context.symbols:
        with contextlib.suppress(Exception):
            _compact_day_partitions(
                dataset_version_id=backtest_dataset_version_id,
                tick_date=timestamp.date(),
                symbols=replay_context.symbols,
            )

    return IngestionReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        dataset_version_id=dataset_version_id,
        symbols_ingested=len(replay_context.symbols),
        missing_bar_incident_count=incident_count,
        rows_written=rows_written,
        summary=result_summary,
    )


def run_corporate_actions_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    source_dataset_version_id: str | None = None,
) -> IngestionReplayResult:
    """Run corporate action ingestion cycle at timestamp.

    Should be called after market ingestion so the source raw bars dataset
    version is available. Uses its own internal session via
    run_corporate_action_ingestion_cycle.
    """
    base: dict[str, Any] = {
        "domain": "corporate_actions",
        "timestamp": timestamp,
        "run_id": str(replay_context.run_id),
    }

    if replay_context.dry_run:
        return IngestionReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    try:
        from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
            run_corporate_action_ingestion_cycle,
        )

        result = run_corporate_action_ingestion_cycle(
            source_raw_bars_dataset_version_id=source_dataset_version_id,
            trigger_type="platform_replay",
            actor=replay_context.actor,
        )
    except Exception as exc:
        return IngestionReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    # The CA cycle produces two artifacts:
    #   dataset_version_id              → corporate_actions events dataset
    #   adjusted_bars_dataset_version_id → the adjusted bars dataset features need
    # Expose the adjusted_bars ID as this result's dataset_version_id so the
    # platform runner can pass it directly to the feature pipeline.
    ca_events_id = str(result.get("dataset_version_id", "")) or None
    adj_bars_id = str(result.get("adjusted_bars_dataset_version_id", "")) or None
    return IngestionReplayResult(
        **base,
        status="ok",
        dataset_version_id=adj_bars_id,  # adjusted_bars version for features
        summary={
            "corporate_actions_dataset_version_id": ca_events_id,
            "adjusted_bars_dataset_version_id": adj_bars_id,
            "source_raw_bars_dataset_version_id": source_dataset_version_id,
            "timestamp": timestamp.isoformat(),
            **{
                k: v
                for k, v in result.items()
                if k not in ("dataset_version_id", "adjusted_bars_dataset_version_id")
            },
        },
    )


def build_ingestion_summary(*, session: Session) -> IngestionSummary:
    """Read latest ingestion state for the platform artifact bundle."""
    from autonomous_trading_platform.contracts.common.enums import PriceBasis

    repo = DatasetVersionsRepository(session)
    raw = repo.get_latest_validated(dataset_name="raw_bars", price_basis=PriceBasis.RAW)
    adj = repo.get_latest_validated(dataset_name="adjusted_bars", price_basis=PriceBasis.ADJUSTED)

    latest_id = raw.dataset_version_id if raw else None
    symbol_coverage = raw.symbol_coverage if raw else 0
    date_range = None
    if raw and raw.date_coverage_start and raw.date_coverage_end:
        date_range = (
            str(raw.date_coverage_start),
            str(raw.date_coverage_end),
        )

    corp_count = 0
    if adj and adj.source_dataset_version:
        corp_count = 1  # corporate actions produced adjusted dataset

    return IngestionSummary(
        latest_raw_dataset_version_id=latest_id,
        symbols_covered=symbol_coverage or 0,
        missing_bar_incident_count=0,
        corporate_actions_ingested=corp_count,
        date_range=date_range,
    )
