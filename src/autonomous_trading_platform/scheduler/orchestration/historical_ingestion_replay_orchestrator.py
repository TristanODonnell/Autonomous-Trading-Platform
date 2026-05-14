"""
HistoricalIngestionReplayOrchestrator

Runtime-cycle debugger that replays a historical time span at 5-minute
intervals, driving the real ingestion cycle (not backfill) at each tick.

Pipeline per tick:
    run_market_ingestion_cycle(now_utc=tick, symbols_override=symbols)
    → resolve active daily raw_bars dataset for tick
    → run_feature_pipeline_cycle(...)
    → run_trading_cycle(now_utc=tick)   [only when run_trading=True]

This mirrors PaperTradingGoldenPathOrchestrator.run_intraday_tick but
advances a historical clock instead of using the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.runtime.clock import HistoricalMarketCalendar
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

_ET = ZoneInfo("America/New_York")

if TYPE_CHECKING:
    pass


@dataclass
class ReplayTickResult:
    tick_utc: datetime
    ingestion_ok: bool = False
    features_ok: bool = False
    trading_ok: bool = False
    raw_dataset_version_id: str | None = None
    error: str | None = None


@dataclass
class HistoricalIngestionReplayResult:
    replay_id: str
    correlation_id: str
    start: datetime
    end: datetime
    cadence_minutes: int
    symbols: list[str]
    market_hours_only: bool
    run_trading: bool
    ticks_attempted: int
    ticks_ingestion_ok: int
    ticks_features_ok: int
    ticks_trading_ok: int
    ticks_failed: int
    ticks_skipped: int
    latest_raw_dataset_version_id: str | None
    warnings: list[str]
    tick_results: list[ReplayTickResult] = field(default_factory=list)


class HistoricalIngestionReplayOrchestrator:
    """
    Replays a historical window tick-by-tick using the real ingestion cycle.

    Unlike BacktestTradingCycleOrchestrator this never calls
    run_market_backfill_cycle.  Each tick fires run_market_ingestion_cycle
    with a historical now_utc, exactly mirroring the live paper trading path.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.runner = RuntimeJobRunner(
            repository=RuntimeJobRunRepository(session),
            failure_notifier=PipelineFailureNotificationService(session),
        )
        self._calendar = HistoricalMarketCalendar()

    # ------------------------------------------------------------------
    # Dataset helpers (same logic as PaperTradingGoldenPathOrchestrator)
    # ------------------------------------------------------------------

    def _get_active_daily_raw_bars_dataset(self, *, now_utc: datetime) -> DatasetVersions | None:
        trading_date = now_utc.date()
        row = (
            self.session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == "raw_bars")
            .filter(DatasetVersions.price_basis == PriceBasis.RAW.value)
            .filter(DatasetVersions.interval == BarInterval.FIVE_MIN.value)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(DatasetVersions.date_coverage_start == trading_date)
            .filter(DatasetVersions.date_coverage_end == trading_date)
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string()
                == "active_daily_incremental"
            )
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )
        return row if isinstance(row, DatasetVersions) else None

    @staticmethod
    def _dataset_symbols(dataset_version: DatasetVersions) -> list[str]:
        source_manifest = dataset_version.source_manifest or {}
        symbols = source_manifest.get("symbols")
        if not isinstance(symbols, list) or not all(isinstance(s, str) and s for s in symbols):
            raise RuntimeError(
                f"raw_bars dataset {dataset_version.dataset_version_id} does not declare symbols"
            )
        return sorted(symbols)

    # ------------------------------------------------------------------
    # Tick generation
    # ------------------------------------------------------------------

    def _generate_ticks(
        self,
        *,
        start: datetime,
        end: datetime,
        cadence_minutes: int,
        market_hours_only: bool,
        max_ticks: int | None,
        session_open_buffer_minutes: int = 0,
        session_close_buffer_minutes: int = 0,
    ) -> list[datetime]:
        ticks: list[datetime] = []
        tick = start
        while tick <= end:
            if max_ticks is not None and len(ticks) >= max_ticks:
                break
            if not market_hours_only or self._is_within_session(
                tick,
                open_buffer=timedelta(minutes=session_open_buffer_minutes),
                close_buffer=timedelta(minutes=session_close_buffer_minutes),
            ):
                ticks.append(tick)
            tick += timedelta(minutes=cadence_minutes)
        return ticks

    def _is_within_session(
        self,
        tick: datetime,
        *,
        open_buffer: timedelta,
        close_buffer: timedelta,
    ) -> bool:
        d = tick.astimezone(_ET).date()
        if not self._calendar.is_trading_day(d):
            return False
        effective_open = self._calendar.market_open(d) + open_buffer
        effective_close = self._calendar.market_close(d) - close_buffer
        return effective_open <= tick < effective_close

    # ------------------------------------------------------------------
    # Per-tick pipeline
    # ------------------------------------------------------------------

    def _run_tick(
        self,
        *,
        tick: datetime,
        symbols: list[str],
        run_trading: bool,
    ) -> ReplayTickResult:
        result = ReplayTickResult(tick_utc=tick)

        # Phase 1: ingestion
        run_market_ingestion_cycle(now_utc=tick, symbols_override=symbols, enforce_lateness=False)
        result.ingestion_ok = True

        # Phase 2: resolve the dataset just written/updated
        self.session.expire_all()
        raw_dataset = self._get_active_daily_raw_bars_dataset(now_utc=tick)
        if raw_dataset is None:
            raise RuntimeError(
                f"No active daily raw_bars dataset found after ingestion for {tick.date()}"
            )
        result.raw_dataset_version_id = raw_dataset.dataset_version_id

        # Phase 3: feature pipeline
        run_feature_pipeline_cycle(
            now_utc=tick,
            price_basis=PriceBasis.RAW,
            dataset_version_id=raw_dataset.dataset_version_id,
            symbols=self._dataset_symbols(raw_dataset),
            start_date=raw_dataset.date_coverage_start,
            end_date=raw_dataset.date_coverage_end,
            include_returns=True,
            include_volatility=False,
            include_moving_average=False,
            include_liquidity=False,
            include_regime=False,
        )
        result.features_ok = True

        # Phase 4: trading (opt-in only — avoids real order submission by default)
        if run_trading:
            run_trading_cycle(now_utc=tick)
            result.trading_ok = True

        return result

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
        cadence_minutes: int = 5,
        market_hours_only: bool = True,
        session_open_buffer_minutes: int = 0,
        session_close_buffer_minutes: int = 0,
        max_ticks: int | None = None,
        run_trading: bool = False,
        stop_on_failure: bool = False,
    ) -> HistoricalIngestionReplayResult:
        replay_id = str(uuid4())
        correlation_id = str(uuid4())
        warnings: list[str] = []

        ticks = self._generate_ticks(
            start=start,
            end=end,
            cadence_minutes=cadence_minutes,
            market_hours_only=market_hours_only,
            max_ticks=max_ticks,
            session_open_buffer_minutes=session_open_buffer_minutes,
            session_close_buffer_minutes=session_close_buffer_minutes,
        )

        if not ticks:
            warnings.append(
                "No ticks generated for the requested window — check start/end and "
                "market_hours_only setting"
            )

        tick_results: list[ReplayTickResult] = []
        ticks_ingestion_ok = 0
        ticks_features_ok = 0
        ticks_trading_ok = 0
        ticks_failed = 0
        latest_raw_dataset_version_id: str | None = None

        def _run_replay() -> None:
            nonlocal ticks_ingestion_ok, ticks_features_ok, ticks_trading_ok
            nonlocal ticks_failed, latest_raw_dataset_version_id

            for tick in ticks:
                try:
                    tick_result = self._run_tick(
                        tick=tick,
                        symbols=symbols,
                        run_trading=run_trading,
                    )
                    tick_results.append(tick_result)

                    if tick_result.ingestion_ok:
                        ticks_ingestion_ok += 1
                    if tick_result.features_ok:
                        ticks_features_ok += 1
                    if tick_result.trading_ok:
                        ticks_trading_ok += 1
                    if tick_result.raw_dataset_version_id is not None:
                        latest_raw_dataset_version_id = tick_result.raw_dataset_version_id

                except Exception as exc:
                    ticks_failed += 1
                    tick_results.append(
                        ReplayTickResult(
                            tick_utc=tick,
                            error=str(exc),
                        )
                    )
                    if stop_on_failure:
                        raise

        self.runner.run(
            job_name="historical_ingestion_replay",
            trigger_type="cli",
            correlation_id=correlation_id,
            input_summary_json={
                "replay_id": replay_id,
                "symbols": symbols,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "cadence_minutes": cadence_minutes,
                "market_hours_only": market_hours_only,
                "session_open_buffer_minutes": session_open_buffer_minutes,
                "session_close_buffer_minutes": session_close_buffer_minutes,
                "run_trading": run_trading,
                "max_ticks": max_ticks,
                "tick_count": len(ticks),
            },
            job=_run_replay,
        )

        return HistoricalIngestionReplayResult(
            replay_id=replay_id,
            correlation_id=correlation_id,
            start=start,
            end=end,
            cadence_minutes=cadence_minutes,
            symbols=symbols,
            market_hours_only=market_hours_only,
            run_trading=run_trading,
            ticks_attempted=len(ticks),
            ticks_ingestion_ok=ticks_ingestion_ok,
            ticks_features_ok=ticks_features_ok,
            ticks_trading_ok=ticks_trading_ok,
            ticks_failed=ticks_failed,
            ticks_skipped=len(ticks) - ticks_ingestion_ok - ticks_failed,
            latest_raw_dataset_version_id=latest_raw_dataset_version_id,
            warnings=warnings,
            tick_results=tick_results,
        )
