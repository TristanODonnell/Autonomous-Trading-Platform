"""
Platform backtest service.

PlatformBacktestRunner orchestrates the full canonical end-to-end replay:

  admin preflight
  → settings snapshot
  → controls snapshot
  → safety snapshot
  → FOR EACH calendar tick:
      ingestion → features → universe (on rotation days) → trading cycle
      → risk → governance → (optional) portfolio snapshot
  → final portfolio snapshot
  → operations snapshot
  → artifact bundle

The runner never shells out to CLI commands. It calls service functions
directly, threading PlatformReplayContext through every hook.

Timeline events (ControlsTimelineEvent, SettingsTimelineEvent, etc.) are
applied at their scheduled timestamps before the trading cycle runs on
that tick.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    ControlsTimelineEvent,
    DomainReplayResult,
    GovernanceTimelineEvent,
    PlatformBacktestArtifact,
    PlatformReplayContext,
    SafetyTimelineEvent,
    SettingsTimelineEvent,
)
from autonomous_trading_platform.db import get_session

logger = logging.getLogger(__name__)

# Domain hooks — imported lazily inside methods to keep module load fast
# and avoid circular imports at import time.


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


@dataclass
class _CheckpointData:
    run_id: str
    replay_id: str
    dataset_version_id: str
    last_completed_tick_index: int  # 1-indexed; ticks up to this index are done
    last_completed_tick_date: str  # ISO date string, for human verification
    cadence_last_run: dict[str, str]  # job → ISO date string
    ticks_ok: int
    ticks_failed: int
    total_orders: int
    total_fills: int


def _write_checkpoint(path: Path, data: _CheckpointData) -> None:
    """Atomically write checkpoint via a temp file + rename."""
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(data), indent=2))
        tmp.replace(path)
    except Exception:
        logger.warning("checkpoint write failed — continuing without checkpoint", exc_info=True)


def _load_checkpoint(path: Path) -> _CheckpointData | None:
    """Load checkpoint; returns None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return _CheckpointData(**raw)
    except Exception:
        logger.warning("checkpoint load failed — starting fresh", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Input / config
# ---------------------------------------------------------------------------


@dataclass
class PlatformBacktestInputs:
    symbols: list[str]
    start_date: date
    end_date: date
    starting_cash: Decimal = Decimal("100000")
    random_seed: int = 42
    actor: str = "platform-backtest"
    dry_run: bool = False
    artifact_dir: Path | None = None
    cadence_minutes: int = 390  # default: one full-day tick per trading day
    universe_rotation_day: int = 0  # weekday for universe rotation (0=Mon)
    governance_tick_every_n_days: int = 1  # run governance every N ticks
    risk_tick_every_n_days: int = 1
    # Optional list of timeline events to apply during replay
    timeline: list[Any] = field(default_factory=list)
    # Failure injection: enabled only when explicitly requested
    inject_failures: bool = False
    failure_injection_schedule: list[Any] = field(default_factory=list)
    # Fixture metadata passed through for artifact annotation
    fixture_name: str | None = None
    scheduled_jobs_config: dict[str, Any] = field(default_factory=dict)
    # Initial state to seed before the first tick (from fixture initial_state block)
    initial_state: Any = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None
    # Checkpoint path: if set, write a checkpoint after every committed tick and
    # resume from it on restart. Set to None to disable (default).
    checkpoint_path: Path | None = None


@dataclass
class TickResult:
    tick_date: str
    tick_timestamp: str
    ingestion: dict[str, Any] | None = None
    corporate_actions: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    universe: dict[str, Any] | None = None
    trading: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    governance: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    operations: dict[str, Any] | None = None
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        # Progress reporting is best-effort UI; it must never affect replay behavior.
        return


# ---------------------------------------------------------------------------
# Market timing
# ---------------------------------------------------------------------------

# Each replay tick is stamped at 4:00 PM ET ≈ 21:00 UTC (standard time / EST).
# This puts ingestion, features, governance, and risk in the correct after-close
# window and gives the trading cycle a realistic same-day reference timestamp.
_MARKET_CLOSE_UTC_HOUR = 21
# Market open: 9:30 AM ET = 14:30 UTC (EST = UTC-5; ignores DST for simplicity).
_MARKET_OPEN_UTC_HOUR = 14
_MARKET_OPEN_UTC_MINUTE = 30


def _market_close_ts(tick_date: date) -> datetime:
    """Return the market-close UTC timestamp for a given trading date (21:00 UTC = 4 PM EST)."""
    return datetime.combine(tick_date, dt_time(_MARKET_CLOSE_UTC_HOUR, 0)).replace(tzinfo=UTC)


def _intraday_bar_timestamps(tick_date: date, cadence_minutes: int) -> list[datetime]:
    """Return bar timestamps for one trading day.

    For cadence_minutes >= 390 (daily), returns a single EOD timestamp.
    For intraday cadence, returns timestamps from 9:30 AM ET to the last bar
    before 4:00 PM ET, spaced cadence_minutes apart.
    """
    if cadence_minutes >= 390:
        return [_market_close_ts(tick_date)]

    market_open = datetime.combine(
        tick_date,
        dt_time(_MARKET_OPEN_UTC_HOUR, _MARKET_OPEN_UTC_MINUTE),
    ).replace(tzinfo=UTC)
    market_close = _market_close_ts(tick_date)

    bars: list[datetime] = []
    ts = market_open
    while ts < market_close:
        bars.append(ts)
        ts = ts + timedelta(minutes=cadence_minutes)
    return bars


# ---------------------------------------------------------------------------
# Cadence scheduler
# ---------------------------------------------------------------------------


class _CadenceScheduler:
    """Determines whether a scheduled job should run on a given trading day.

    Reads the scheduled_jobs config dict (from fixture or PlatformBacktestInputs)
    and tracks the last run date per job to enforce cadences accurately.

    Cadences:
      daily          → every trading day
      per_tick       → every trading day (alias for daily)
      after_ingestion→ only on ticks where ingestion ran
      weekly         → first trading day of each ISO week (Monday)
      biweekly       → first trading day of every other week (Monday, ≥14 days gap)
      monthly        → first tick of each calendar month
    """

    def __init__(self, scheduled_jobs: dict[str, Any]) -> None:
        self._cfg = scheduled_jobs
        self._last_run: dict[str, date] = {}

    def should_run(self, job: str, tick_date: date, *, ingestion_ran: bool = False) -> bool:
        cfg = self._cfg.get(job, {})
        if isinstance(cfg, dict):
            enabled = cfg.get("enabled", True)
            cadence = cfg.get("cadence", "daily")
        else:
            # ScheduledJobConfig Pydantic object
            enabled = getattr(cfg, "enabled", True)
            cadence = getattr(cfg, "cadence", "daily")

        if not enabled:
            return False

        last = self._last_run.get(job)

        if cadence in ("daily", "per_tick"):
            return True
        if cadence == "after_ingestion":
            return ingestion_ran
        if cadence == "weekly":
            # Run on Monday of each trading week; if Monday was a holiday,
            # fire on the first available day that week (weekday ≤ 1 = Mon/Tue).
            if last is None:
                return True
            days_since = (tick_date - last).days
            return tick_date.weekday() == 0 or (tick_date.weekday() == 1 and days_since >= 6)
        if cadence == "biweekly":
            if last is None:
                return tick_date.weekday() <= 1
            days_since = (tick_date - last).days
            return days_since >= 14 and tick_date.weekday() <= 1
        if cadence == "monthly":
            # First trading tick of each calendar month
            if last is None:
                return True
            return last.month != tick_date.month or last.year != tick_date.year
        return True  # unknown cadence → always run

    def record(self, job: str, tick_date: date) -> None:
        self._last_run[job] = tick_date


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PlatformBacktestRunner:
    """Orchestrates the full platform backtest replay."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session

    def run(self, inputs: PlatformBacktestInputs) -> PlatformBacktestArtifact:
        # Order throttle limits are designed for live/paper trading (2–5 orders per bar).
        # In a backtest with N symbols we may generate N order intents in a single tick.
        # Raise the limits for the duration of this run so the safety gate doesn't block
        # legitimate backtest order flow.  Restored on exit regardless of outcome.
        _throttle_keys = {
            "MAX_ORDERS_PER_BAR": "1000",
            "MAX_ORDERS_PER_HOUR": "100000",
            "SHADOW_MODE_ENABLED": "false",  # backtest always uses simulated execution
        }
        _saved_throttle = {k: os.environ.get(k) for k in _throttle_keys}
        for k, v in _throttle_keys.items():
            os.environ[k] = v

        try:
            return self._run(inputs)
        finally:
            for k, prev in _saved_throttle.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    def _run(self, inputs: PlatformBacktestInputs) -> PlatformBacktestArtifact:
        started_at = datetime.now(UTC)

        # ── Checkpoint: resume or fresh start ──────────────────────────────
        checkpoint = _load_checkpoint(inputs.checkpoint_path) if inputs.checkpoint_path else None
        resume_from_tick = 0  # ticks <= this index are skipped (already done)

        if checkpoint is not None:
            logger.info(
                "Resuming backtest from checkpoint: tick %d (%s)",
                checkpoint.last_completed_tick_index,
                checkpoint.last_completed_tick_date,
            )
            ctx = PlatformReplayContext(
                run_id=uuid.UUID(checkpoint.run_id),
                replay_id=checkpoint.replay_id,
                timestamp=datetime.combine(inputs.start_date, datetime.min.time()).replace(
                    tzinfo=UTC
                ),
                symbols=inputs.symbols,
                actor=inputs.actor,
                dry_run=inputs.dry_run,
                artifact_dir=inputs.artifact_dir,
                dataset_version_id=checkpoint.dataset_version_id,
            )
            resume_from_tick = checkpoint.last_completed_tick_index
        else:
            ctx = PlatformReplayContext.create(
                symbols=inputs.symbols,
                timestamp=datetime.combine(inputs.start_date, datetime.min.time()).replace(
                    tzinfo=UTC
                ),
                actor=inputs.actor,
                dry_run=inputs.dry_run,
                artifact_dir=inputs.artifact_dir,
            )

        session: Session = self._session_factory()
        tick_results: list[dict[str, Any]] = []
        timeline_events_applied: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        all_errors: list[str] = []

        # Aggregate runtime counters — restored from checkpoint when resuming
        ticks_attempted = 0
        ticks_ok = checkpoint.ticks_ok if checkpoint else 0
        ticks_failed = checkpoint.ticks_failed if checkpoint else 0
        total_orders = checkpoint.total_orders if checkpoint else 0
        total_fills = checkpoint.total_fills if checkpoint else 0
        run_ids: list[str] = []

        # raw_bars version from ingestion → passed to corporate_actions and failure injections
        latest_dataset_version_id: str | None = None

        # Summaries populated in phases 1-4; None when resuming (not needed for artifact)
        admin_summary = None
        settings_summary = None

        try:
            if checkpoint is None:
                # ── Phase 1: preflight ──────────────────────────────────────
                from autonomous_trading_platform.application.services.platform_replay.admin_hooks import (
                    build_admin_preflight_summary,
                    validate_admin_preflight,
                )

                preflight = validate_admin_preflight(
                    session=session, timestamp=ctx.timestamp, replay_context=ctx
                )
                if not preflight.ok:
                    all_errors.extend(preflight.errors)

                admin_summary = build_admin_preflight_summary(session=session)

                # ── Phase 2: settings snapshot ──────────────────────────────
                from autonomous_trading_platform.application.services.platform_replay.settings_hooks import (
                    build_settings_summary,
                    snapshot_settings_at_timestamp,
                )

                snapshot_settings_at_timestamp(
                    session=session, timestamp=ctx.timestamp, replay_context=ctx
                )
                settings_summary = build_settings_summary(session=session)

                # ── Phase 3: controls snapshot ──────────────────────────────
                from autonomous_trading_platform.application.services.platform_replay.controls_hooks import (
                    snapshot_controls_at_timestamp,
                )

                snapshot_controls_at_timestamp(
                    session=session, timestamp=ctx.timestamp, replay_context=ctx
                )

                # ── Phase 4: safety snapshot ────────────────────────────────
                from autonomous_trading_platform.application.services.platform_replay.safety_hooks import (
                    build_safety_summary,
                    snapshot_safety_at_timestamp,
                )

                snapshot_safety_at_timestamp(
                    session=session, timestamp=ctx.timestamp, replay_context=ctx
                )

                # ── Phase 4a: clear stale evaluation checkpoints ───────────
                if not inputs.dry_run:
                    from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
                        StrategyRuntimeState as _SRS,
                    )

                    session.query(_SRS).update(
                        {_SRS.last_evaluated_bar_timestamp: None},
                        synchronize_session=False,
                    )
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()

                # ── Phase 4b: initial state seeding ────────────────────────
                if inputs.initial_state is not None and not inputs.dry_run:
                    from autonomous_trading_platform.application.services.platform_replay.initial_state_hooks import (
                        apply_initial_state,
                    )

                    _initial_state_summary = apply_initial_state(
                        session=session,
                        initial_state=inputs.initial_state,
                        timestamp=ctx.timestamp,
                        actor=inputs.actor,
                    )
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                    if _initial_state_summary.get("errors"):
                        all_warnings.extend(
                            [f"initial_state: {e}" for e in _initial_state_summary["errors"]]
                        )

                # ── Phase 4c: universe bootstrap ────────────────────────────
                if not inputs.dry_run:
                    from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
                        UniverseVersionRepository as _UVRepo,
                    )

                    _bootstrap_ts = datetime.combine(inputs.start_date, dt_time.min).replace(
                        tzinfo=UTC
                    )
                    _uv_repo = _UVRepo(session)
                    _uv_repo.retire_active_version(_bootstrap_ts)
                    session.commit()
                    if True:
                        from autonomous_trading_platform.storage.sor.repositories.core.raw_market_pool_repository import (
                            RawMarketPoolRepository as _RMPRepo,
                        )
                        from autonomous_trading_platform.universe.jobs.run_candidate_generation import (
                            run_candidate_generation as _run_cg,
                        )
                        from autonomous_trading_platform.universe.jobs.run_universe_rotation import (
                            run_universe_rotation as _run_ur,
                        )
                        from autonomous_trading_platform.universe.providers.alpaca_screener_provider import (
                            AlpacaScreenerProvider as _Screener,
                        )
                        from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
                            RawMarketPoolRefreshService as _RMPSvc,
                        )
                        from autonomous_trading_platform.universe.services.universe_version_service import (
                            UniverseVersionService as _UVService,
                        )
                        from autonomous_trading_platform.universe.types import (
                            CandidateGenerationConfig as _CGConfig,
                        )
                        from autonomous_trading_platform.universe.types import (
                            UniverseRebalanceConfig as _URConfig,
                        )

                        _screener_records: list | None = None
                        try:
                            _screener = _Screener(as_of=inputs.start_date, top_n=500)
                            _screener_records = _screener.fetch_symbols()
                            _pool_repo = _RMPRepo(session)
                            _refresh_svc = _RMPSvc(_pool_repo, _screener)
                            _refresh_svc.refresh(cadence="bootstrap", captured_at=_bootstrap_ts)
                            session.commit()
                        except Exception as _exc:
                            all_warnings.append(f"universe_screener_failed: {_exc}")

                        _candidate_ok = False
                        if _screener_records:
                            try:
                                _run_cg(
                                    as_of=_bootstrap_ts,
                                    config=_CGConfig(
                                        as_of=_bootstrap_ts, lookback_days=20, max_symbols=500
                                    ),
                                    rebalance_reason="backtest_bootstrap",
                                )
                                _run_ur(
                                    candidate_version_id=None,
                                    config=_URConfig(),
                                    rotation_reason="backtest_bootstrap",
                                    force_rotation=True,
                                    as_of=_bootstrap_ts,
                                    skip_cadence_check=True,
                                )
                                _candidate_ok = True
                            except Exception as _exc:
                                all_warnings.append(f"universe_candidate_rotation_failed: {_exc}")

                        if not _candidate_ok:
                            try:
                                _uv_repo2 = _UVRepo(session)
                                _uv_svc = _UVService(_uv_repo2)
                                if True:
                                    _target_size = _URConfig().target_universe_size
                                    _ingested_symbols = set(inputs.symbols)
                                    if _screener_records:
                                        _filtered_records = [
                                            r
                                            for r in _screener_records
                                            if r.symbol in _ingested_symbols
                                        ]
                                        _bootstrap_symbols = [
                                            r.symbol for r in _filtered_records[:_target_size]
                                        ] or list(_ingested_symbols)[:_target_size]
                                        _fallback_source = "screener_bootstrap"
                                        _fallback_reason = "no_bar_data_for_candidate_scoring"
                                    else:
                                        _bootstrap_symbols = inputs.symbols
                                        _fallback_source = "yaml_bootstrap_fallback"
                                        _fallback_reason = "screener_unavailable"
                                    _bv, _bm = _uv_svc.build_version(
                                        name=f"bootstrap_{inputs.start_date.isoformat()}",
                                        effective_from=_bootstrap_ts,
                                        symbols=_bootstrap_symbols,
                                        source=_fallback_source,
                                        rebalance_reason=_fallback_reason,
                                    )
                                    _uv_repo2.insert_version(_bv)
                                    session.flush()
                                    _uv_repo2.insert_members(_bm)
                                    session.flush()
                                    _uv_repo2.activate_version(_bv.universe_version_id)
                                    session.commit()
                            except Exception:
                                session.rollback()
            else:
                # ── Resuming: phases 1-4 already committed ──────────────────
                logger.info(
                    "Resuming from checkpoint at tick %d (%s) — skipping setup phases",
                    checkpoint.last_completed_tick_index,
                    checkpoint.last_completed_tick_date,
                )
                from autonomous_trading_platform.application.services.platform_replay.safety_hooks import (
                    build_safety_summary,
                )

            # ── Phase 5: tick loop ──────────────────────────────────────────
            trading_dates = _trading_dates(inputs.start_date, inputs.end_date)
            total_ticks = len(trading_dates)

            # Build cadence scheduler from fixture scheduled_jobs config
            cadence = _CadenceScheduler(inputs.scheduled_jobs_config)

            if checkpoint is not None:
                # Restore cadence state so weekly/monthly jobs fire at correct intervals
                cadence._last_run = {
                    k: date.fromisoformat(v) for k, v in checkpoint.cadence_last_run.items()
                }
                backtest_dataset_version_id = checkpoint.dataset_version_id
                ctx.dataset_version_id = backtest_dataset_version_id
            else:
                # Create one cumulative dataset version for the entire backtest run.
                backtest_dataset_version_id = _create_backtest_dataset_version(
                    session=session,
                    inputs=inputs,
                    ctx=ctx,
                )
                ctx.dataset_version_id = backtest_dataset_version_id

            for tick_index, tick_date in enumerate(trading_dates, start=1):
                # Stamp each tick at market close (21:00 UTC = 4 PM ET)
                tick_ts = _market_close_ts(tick_date)
                _emit_progress(
                    inputs.progress_callback,
                    {
                        "status": "running",
                        "current_tick": tick_index,
                        "completed_ticks": tick_index - 1,
                        "total_ticks": total_ticks,
                        "tick_date": tick_date.isoformat(),
                        "start_date": inputs.start_date.isoformat(),
                        "end_date": inputs.end_date.isoformat(),
                        "ticks_ok": ticks_ok,
                        "ticks_failed": ticks_failed,
                        "total_orders": total_orders,
                        "total_fills": total_fills,
                    },
                )
                # ── Skip already-completed ticks on resume ─────────────────
                if tick_index <= resume_from_tick:
                    tick_results.append(
                        {
                            "tick_date": tick_date.isoformat(),
                            "tick_timestamp": tick_ts.isoformat(),
                            "skipped_by_checkpoint": True,
                        }
                    )
                    ticks_attempted += 1
                    continue

                tick_ctx = PlatformReplayContext(
                    run_id=ctx.run_id,
                    replay_id=ctx.replay_id,
                    timestamp=tick_ts,
                    symbols=ctx.symbols,
                    actor=ctx.actor,
                    dry_run=ctx.dry_run,
                    artifact_dir=ctx.artifact_dir,
                    dataset_version_id=ctx.dataset_version_id,
                )

                tick = TickResult(
                    tick_date=tick_date.isoformat(),
                    tick_timestamp=tick_ts.isoformat(),
                )
                ticks_attempted += 1

                # ── Timeline events (applied before any jobs) ───────────────
                for event in _events_for_date(inputs.timeline, tick_date):
                    event_result = _apply_timeline_event(
                        session=session,
                        timestamp=tick_ts,
                        event=event,
                        replay_context=tick_ctx,
                    )
                    ev_entry: dict[str, Any] = {
                        "event_type": getattr(event, "event_type", str(event)),
                        "status": event_result.status,
                    }
                    if event_result.errors:
                        ev_entry["errors"] = event_result.errors
                        tick.errors.extend(event_result.errors)
                    tick.timeline_events.append(ev_entry)
                    timeline_events_applied.append(
                        {
                            "date": tick_date.isoformat(),
                            "event_type": getattr(event, "event_type", str(event)),
                            "status": event_result.status,
                        }
                    )

                # ── Failure injections (explicit opt-in only) ───────────────
                if inputs.inject_failures:
                    for fi_event in _events_for_date(inputs.failure_injection_schedule, tick_date):
                        fi_result = _dispatch_failure_injection(
                            session=session,
                            event=fi_event,
                            timestamp=tick_ts,
                            symbols=inputs.symbols,
                            replay_run_id=ctx.replay_id,
                            dataset_version=latest_dataset_version_id,  # raw_bars — registered in SOR
                            run_id=str(tick_ctx.run_id),
                        )
                        tick.timeline_events.append(
                            {
                                "event_type": "failure_injected",
                                "target": getattr(fi_event, "target", None),
                                "failure": getattr(fi_event, "failure", None),
                                "status": fi_result.get("status"),
                            }
                        )
                        if fi_result.get("status") == "error":
                            tick.errors.append(
                                f"Failure injection error [{fi_event.target}/{fi_event.failure}]: "
                                f"{fi_result.get('detail')}"
                            )
                        else:
                            all_warnings.append(
                                f"[INJECTED] {fi_event.target}/{fi_event.failure} at {tick_date}"
                            )

                is_intraday = inputs.cadence_minutes < 390

                # ── Ingestion (always fetch the full trading day in replay) ──
                ingestion_ran = False
                if cadence.should_run("ingestion", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
                        run_ingestion_at_timestamp,
                    )

                    ing_result = run_ingestion_at_timestamp(
                        session=session,
                        timestamp=tick_ts,
                        replay_context=tick_ctx,
                        full_day=True,  # always fetch the full day in historical replay
                        backtest_dataset_version_id=backtest_dataset_version_id,
                    )
                    tick.ingestion = ing_result.summary
                    if ing_result.dataset_version_id:
                        latest_dataset_version_id = ing_result.dataset_version_id
                        ingestion_ran = True
                    if ing_result.errors:
                        tick.errors.extend(ing_result.errors)
                    all_warnings.extend(ing_result.warnings)
                    cadence.record("ingestion", tick_date)

                # ── Corporate actions (daily — after ingestion) ─────────────
                if cadence.should_run("corporate_actions", tick_date, ingestion_ran=ingestion_ran):
                    from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
                        run_corporate_actions_at_timestamp,
                    )

                    ca_result = run_corporate_actions_at_timestamp(
                        session=session,
                        timestamp=tick_ts,
                        replay_context=tick_ctx,
                        source_dataset_version_id=latest_dataset_version_id,
                    )
                    tick.corporate_actions = ca_result.summary
                    if ca_result.errors:
                        tick.errors.extend(ca_result.errors)
                    cadence.record("corporate_actions", tick_date)

                # ── Features (daily mode only — runs before trading cycle) ──
                if not is_intraday and cadence.should_run(
                    "features", tick_date, ingestion_ran=ingestion_ran
                ):
                    from autonomous_trading_platform.application.services.platform_replay.features_hooks import (
                        run_features_at_timestamp,
                    )

                    feat_result = run_features_at_timestamp(
                        session=session,
                        timestamp=tick_ts,
                        dataset_version_id=None,
                        symbols=inputs.symbols,
                        replay_context=tick_ctx,
                    )
                    tick.features = feat_result.summary
                    if feat_result.errors:
                        tick.errors.extend(feat_result.errors)
                    all_warnings.extend(feat_result.warnings)
                    cadence.record("features", tick_date)

                # ── Universe selection/rotation (monthly by default) ─────────
                if cadence.should_run("universe", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.universe_hooks import (
                        run_universe_at_timestamp,
                    )

                    uni_result = run_universe_at_timestamp(
                        session=session,
                        timestamp=tick_ts,
                        replay_context=tick_ctx,
                        skip_cadence_check=True,
                    )
                    tick.universe = uni_result.summary
                    cadence.record("universe", tick_date)

                # ── Trading cycle ────────────────────────────────────────────
                if cadence.should_run("trading_cycle", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.features_hooks import (
                        run_features_at_timestamp,
                    )
                    from autonomous_trading_platform.application.services.platform_replay.runtime_hooks import (
                        _build_simulated_broker_client,
                        run_trading_cycle_at_timestamp,
                    )

                    bar_timestamps = _intraday_bar_timestamps(tick_date, inputs.cadence_minutes)

                    # One broker client per day so cash/position state persists
                    # across all intraday ticks. Daily mode builds per tick (backwards-compat).
                    day_broker_client = (
                        _build_simulated_broker_client(
                            session=session,
                            timestamp=bar_timestamps[0],
                            dataset_version_id=backtest_dataset_version_id,
                        )
                        if is_intraday
                        else None
                    )

                    last_trading_result = None
                    for bar_ts in bar_timestamps:
                        bar_ctx = PlatformReplayContext(
                            run_id=tick_ctx.run_id,
                            replay_id=tick_ctx.replay_id,
                            timestamp=bar_ts,
                            symbols=tick_ctx.symbols,
                            actor=tick_ctx.actor,
                            dry_run=tick_ctx.dry_run,
                            artifact_dir=tick_ctx.artifact_dir,
                            dataset_version_id=tick_ctx.dataset_version_id,
                        )

                        # Features per tick in intraday mode — uses bars up to bar_ts date
                        if is_intraday and cadence.should_run(
                            "features", tick_date, ingestion_ran=ingestion_ran
                        ):
                            feat_result = run_features_at_timestamp(
                                session=session,
                                timestamp=bar_ts,
                                dataset_version_id=None,
                                symbols=inputs.symbols,
                                replay_context=bar_ctx,
                            )
                            if feat_result.errors:
                                tick.errors.extend(feat_result.errors)
                            if tick.features is None:
                                tick.features = feat_result.summary

                        bar_result = run_trading_cycle_at_timestamp(
                            session=session,
                            timestamp=bar_ts,
                            replay_context=bar_ctx,
                            broker_client=day_broker_client,
                            backtest_dataset_version_id=backtest_dataset_version_id,
                        )
                        total_orders += bar_result.orders_submitted
                        total_fills += bar_result.fills_received
                        last_trading_result = bar_result
                        if str(tick_ctx.run_id) not in run_ids:
                            run_ids.append(str(tick_ctx.run_id))

                    # Record features cadence after the loop (intraday ran it per tick)
                    if is_intraday:
                        cadence.record("features", tick_date)

                    if last_trading_result is not None:
                        tick.trading = last_trading_result.summary
                    cadence.record("trading_cycle", tick_date)

                # ── Risk evaluation (daily — EOD) ───────────────────────────
                if cadence.should_run("risk", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.risk_hooks import (
                        run_risk_at_timestamp,
                    )

                    risk_result = run_risk_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.risk = risk_result.summary
                    cadence.record("risk", tick_date)

                # ── Governance automation (daily — EOD) ─────────────────────
                if cadence.should_run("governance", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.governance_hooks import (
                        run_governance_at_timestamp,
                    )

                    gov_result = run_governance_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.governance = gov_result.summary
                    cadence.record("governance", tick_date)

                # ── Portfolio snapshot (daily) ───────────────────────────────
                if cadence.should_run("portfolio_snapshot", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.portfolio_hooks import (
                        snapshot_portfolio_at_timestamp,
                    )

                    port_result = snapshot_portfolio_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.portfolio = port_result.summary
                    cadence.record("portfolio_snapshot", tick_date)

                # ── Research / experiment pipeline (monthly) ─────────────────
                if cadence.should_run("research", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
                        run_scheduled_research_at_timestamp,
                    )

                    res_result = run_scheduled_research_at_timestamp(
                        session=session,
                        timestamp=tick_ts,
                        replay_context=tick_ctx,
                        dataset_version_id=backtest_dataset_version_id,
                    )
                    tick.research = res_result.summary
                    if res_result.errors:
                        tick.errors.extend(res_result.errors)
                    cadence.record("research", tick_date)

                # ── Operations health (daily) ────────────────────────────────
                if cadence.should_run("operations_health", tick_date):
                    from autonomous_trading_platform.application.services.platform_replay.operations_hooks import (
                        snapshot_operations_at_timestamp,
                    )

                    ops_result = snapshot_operations_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.operations = ops_result.summary
                    cadence.record("operations_health", tick_date)

                # ── Commit tick ─────────────────────────────────────────────
                if not inputs.dry_run:
                    try:
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        tick.errors.append(f"Commit failed: {exc}")

                if tick.errors:
                    ticks_failed += 1
                    all_errors.extend(tick.errors)
                else:
                    ticks_ok += 1
                    # Write checkpoint after every clean commit so a restart
                    # can resume from this tick rather than from tick 1.
                    if inputs.checkpoint_path and not inputs.dry_run:
                        _write_checkpoint(
                            inputs.checkpoint_path,
                            _CheckpointData(
                                run_id=str(ctx.run_id),
                                replay_id=ctx.replay_id,
                                dataset_version_id=backtest_dataset_version_id,
                                last_completed_tick_index=tick_index,
                                last_completed_tick_date=tick_date.isoformat(),
                                cadence_last_run={
                                    k: v.isoformat() for k, v in cadence._last_run.items()
                                },
                                ticks_ok=ticks_ok,
                                ticks_failed=ticks_failed,
                                total_orders=total_orders,
                                total_fills=total_fills,
                            ),
                        )

                tick_results.append({k: v for k, v in tick.__dict__.items()})
                _emit_progress(
                    inputs.progress_callback,
                    {
                        "status": "completed",
                        "current_tick": tick_index,
                        "completed_ticks": tick_index,
                        "total_ticks": total_ticks,
                        "tick_date": tick_date.isoformat(),
                        "start_date": inputs.start_date.isoformat(),
                        "end_date": inputs.end_date.isoformat(),
                        "ticks_ok": ticks_ok,
                        "ticks_failed": ticks_failed,
                        "total_orders": total_orders,
                        "total_fills": total_fills,
                    },
                )

            # ── Phase 6: final snapshots ────────────────────────────────────
            end_ts = datetime.combine(inputs.end_date, datetime.min.time()).replace(tzinfo=UTC)
            end_ctx = PlatformReplayContext(
                run_id=ctx.run_id,
                replay_id=ctx.replay_id,
                timestamp=end_ts,
                symbols=ctx.symbols,
                actor=ctx.actor,
                dry_run=ctx.dry_run,
                artifact_dir=ctx.artifact_dir,
                dataset_version_id=ctx.dataset_version_id,
            )

            from autonomous_trading_platform.application.services.platform_replay.portfolio_hooks import (
                build_portfolio_summary,
            )

            portfolio_summary = build_portfolio_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.operations_hooks import (
                build_operations_summary,
            )

            operations_summary = build_operations_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.risk_hooks import (
                build_risk_summary,
            )

            risk_summary = build_risk_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.governance_hooks import (
                build_governance_summary,
            )

            governance_summary = build_governance_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.controls_hooks import (
                build_controls_summary,
            )

            controls_summary = build_controls_summary(session=session)

            safety_summary = build_safety_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
                build_ingestion_summary,
            )

            ingestion_summary = build_ingestion_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.features_hooks import (
                build_feature_summary,
            )

            feature_summary = build_feature_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.universe_hooks import (
                build_universe_summary,
            )

            universe_summary = build_universe_summary(session=session, timestamp=end_ts)

            from autonomous_trading_platform.application.services.platform_replay.diagnostics_hooks import (
                build_diagnostics_summary,
            )

            diagnostics_summary = build_diagnostics_summary(
                session=session, timestamp=end_ts, replay_context=end_ctx
            )

            from autonomous_trading_platform.application.services.platform_replay.execution_hooks import (
                build_execution_summary,
            )

            execution_summary = build_execution_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
                build_research_summary,
            )

            research_summary = build_research_summary(session=session)

            from autonomous_trading_platform.application.services.platform_replay.strategy_hooks import (
                build_strategy_catalog_summary,
            )

            strategy_catalog_summary = build_strategy_catalog_summary(session=session)

        finally:
            session.close()

        completed_at = datetime.now(UTC)

        runtime_summary_obj = None
        try:
            from autonomous_trading_platform.contracts.runtime.platform_replay import RuntimeSummary

            runtime_summary_obj = RuntimeSummary(
                ticks_attempted=ticks_attempted,
                ticks_ok=ticks_ok,
                ticks_failed=ticks_failed,
                total_orders=total_orders,
                total_fills=total_fills,
                run_ids=run_ids,
            )
        except Exception:
            pass

        return PlatformBacktestArtifact(
            replay_id=ctx.replay_id,
            run_id=str(ctx.run_id),
            actor=ctx.actor,
            dry_run=ctx.dry_run,
            symbols=ctx.symbols,
            start_date=inputs.start_date.isoformat(),
            end_date=inputs.end_date.isoformat(),
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            fixture_name=inputs.fixture_name,
            inject_failures=inputs.inject_failures,
            admin=admin_summary,
            settings=settings_summary,
            controls=controls_summary,
            safety=safety_summary,
            universe=universe_summary,
            ingestion=ingestion_summary,
            features=feature_summary,
            runtime=runtime_summary_obj,
            risk=risk_summary,
            governance=governance_summary,
            portfolio=portfolio_summary,
            operations=operations_summary,
            execution=execution_summary,
            research=research_summary,
            diagnostics=diagnostics_summary,
            strategy_catalog=strategy_catalog_summary,
            tick_results=tick_results,
            timeline_events_applied=timeline_events_applied,
            warnings=all_warnings,
            errors=all_errors,
        )

    def plan(self, inputs: PlatformBacktestInputs) -> dict[str, Any]:
        """Validate inputs and return a cadence-aware structured plan without executing."""
        from autonomous_trading_platform.platform.replay.platform_replay_config import (
            MergedReplayParams,
            OutputConfig,
            validate_plan,
        )

        params = MergedReplayParams(
            symbols=inputs.symbols,
            start_date=inputs.start_date,
            end_date=inputs.end_date,
            starting_cash=inputs.starting_cash,
            random_seed=inputs.random_seed,
            fixture=None,
            scheduled_jobs=inputs.scheduled_jobs_config,  # type: ignore[arg-type]
            timeline_events=[],
            failure_injections=inputs.failure_injection_schedule,  # type: ignore[arg-type]
            outputs=OutputConfig(),
        )
        result = validate_plan(params)

        # Simulate cadence fire-counts so plan output shows exactly how many
        # times each job will run over the replay window
        trading_dates = _trading_dates(inputs.start_date, inputs.end_date)
        sched = _CadenceScheduler(inputs.scheduled_jobs_config)
        job_fire_counts: dict[str, int] = {}
        for td in trading_dates:
            # Determine if ingestion will run this tick (for after_ingestion deps)
            ingestion_ran_sim = sched.should_run("ingestion", td)
            for job in inputs.scheduled_jobs_config:
                if sched.should_run(job, td, ingestion_ran=ingestion_ran_sim):
                    job_fire_counts[job] = job_fire_counts.get(job, 0) + 1
                    sched.record(job, td)

        result["fixture_name"] = inputs.fixture_name
        result["tick_timestamp_model"] = "market_close_21:00_UTC"
        result["scheduled_job_fire_counts"] = job_fire_counts
        result["failure_injections_enabled"] = inputs.inject_failures
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trading_dates(start: date, end: date) -> list[date]:
    """Return weekdays (Mon–Fri) in [start, end]."""
    dates: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _events_for_date(
    timeline: list[Any],
    tick_date: date,
) -> list[Any]:
    """Return events scheduled for tick_date.

    Handles both typed event dataclasses (field: scheduled_date) and raw
    TimelineEventConfig objects from the fixture parser (field: at).
    """
    result: list[Any] = []
    for event in timeline:
        # Typed event dataclasses use scheduled_date; fixture configs use at
        raw = getattr(event, "scheduled_date", None) or getattr(event, "at", None)
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                raw = date.fromisoformat(raw)
            except ValueError:
                continue
        if raw == tick_date:
            result.append(event)
    return result


def _dispatch_failure_injection(
    *,
    session: Session,
    event: Any,
    timestamp: datetime,
    symbols: list[str],
    replay_run_id: str,
    dataset_version: str | None,
    run_id: str,
) -> dict[str, Any]:
    """Delegate a failure injection event to the config module dispatcher."""
    from autonomous_trading_platform.platform.replay.platform_replay_config import (
        dispatch_failure_injection,
    )

    return dispatch_failure_injection(
        session=session,
        event=event,
        timestamp=timestamp,
        symbols=symbols,
        replay_run_id=replay_run_id,
        dataset_version=dataset_version,
        run_id=run_id,
    )


def _apply_timeline_event(
    *,
    session: Session,
    timestamp: datetime,
    event: Any,
    replay_context: PlatformReplayContext,
) -> DomainReplayResult:
    """Dispatch a timeline event to the correct domain hook."""
    if isinstance(event, SafetyTimelineEvent):
        from autonomous_trading_platform.application.services.platform_replay.safety_hooks import (
            apply_safety_event,
        )

        return apply_safety_event(
            session=session, timestamp=timestamp, event=event, replay_context=replay_context
        )

    if isinstance(event, ControlsTimelineEvent):
        from autonomous_trading_platform.application.services.platform_replay.controls_hooks import (
            apply_controls_event,
        )

        return apply_controls_event(
            session=session, timestamp=timestamp, event=event, replay_context=replay_context
        )

    if isinstance(event, SettingsTimelineEvent):
        from autonomous_trading_platform.application.services.platform_replay.settings_hooks import (
            apply_settings_event,
        )

        return apply_settings_event(
            session=session, timestamp=timestamp, event=event, replay_context=replay_context
        )

    if isinstance(event, GovernanceTimelineEvent):
        from autonomous_trading_platform.application.services.platform_replay.governance_hooks import (
            apply_governance_event,
        )

        return apply_governance_event(
            session=session, timestamp=timestamp, event=event, replay_context=replay_context
        )

    from autonomous_trading_platform.contracts.runtime.platform_replay import DomainReplayResult

    return DomainReplayResult(
        domain="unknown",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
        status="skipped",
        warnings=[f"Unknown timeline event type: {type(event).__name__}"],
    )


def _create_backtest_dataset_version(
    *,
    session: Session,
    inputs: PlatformBacktestInputs,
    ctx: PlatformReplayContext,
) -> str:
    """Register one cumulative raw_bars dataset version for the entire backtest run.

    All daily ingestion writes accumulate here (via bar_chunk_writer_service merge
    logic). Research reads the latest validated version and gets the full growing
    bar history up to the current research date.
    """
    from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
    from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
    from autonomous_trading_platform.runtime.services.dataset_registration_service import (
        DatasetRegistrationService,
    )
    from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
    from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version

    dataset_version_id = generate_dataset_version("raw_bars")
    dv = DatasetVersion(
        dataset_version_id=dataset_version_id,
        dataset_name=RAW_BARS_DATASET.dataset_key,
        created_at=ctx.timestamp,
        source="platform_backtest",
        price_basis=PriceBasis.RAW,
        interval=BarInterval.FIVE_MIN,
        schema_version=RAW_BARS_DATASET.schema_version,
        symbol_coverage=len(inputs.symbols),
        date_coverage_start=inputs.start_date,
        date_coverage_end=inputs.end_date,
        validation_status="validated",
        checksum="platform_backtest_cumulative",
        source_manifest={
            "pipeline": "platform_backtest",
            "symbols": sorted(inputs.symbols),
            "start_date": inputs.start_date.isoformat(),
            "end_date": inputs.end_date.isoformat(),
        },
        metadata_json={
            "dataset_lifecycle": "platform_backtest_cumulative",
            "replay_run_id": str(ctx.replay_id),
        },
    )

    svc = DatasetRegistrationService(session=session)
    svc.save(dv)  # save() bypasses schema validation (checksum rule doesn't apply here)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    return dataset_version_id
