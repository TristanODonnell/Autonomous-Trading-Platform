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

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
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

# Domain hooks — imported lazily inside methods to keep module load fast
# and avoid circular imports at import time.


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


@dataclass
class TickResult:
    tick_date: str
    tick_timestamp: str
    ingestion: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    universe: dict[str, Any] | None = None
    trading: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    governance: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PlatformBacktestRunner:
    """Orchestrates the full platform backtest replay."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session

    def run(self, inputs: PlatformBacktestInputs) -> PlatformBacktestArtifact:
        started_at = datetime.now(UTC)

        ctx = PlatformReplayContext.create(
            symbols=inputs.symbols,
            timestamp=datetime.combine(inputs.start_date, datetime.min.time()).replace(tzinfo=UTC),
            actor=inputs.actor,
            dry_run=inputs.dry_run,
            artifact_dir=inputs.artifact_dir,
        )

        session: Session = self._session_factory()
        tick_results: list[dict[str, Any]] = []
        timeline_events_applied: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        all_errors: list[str] = []

        # Aggregate runtime counters
        ticks_attempted = 0
        ticks_ok = 0
        ticks_failed = 0
        total_orders = 0
        total_fills = 0
        run_ids: list[str] = []

        # Latest dataset version from ingestion (threaded into features)
        latest_dataset_version_id: str | None = None

        try:
            # ── Phase 1: preflight ──────────────────────────────────────────
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

            # ── Phase 2: settings snapshot ──────────────────────────────────
            from autonomous_trading_platform.application.services.platform_replay.settings_hooks import (
                build_settings_summary,
                snapshot_settings_at_timestamp,
            )

            snapshot_settings_at_timestamp(
                session=session, timestamp=ctx.timestamp, replay_context=ctx
            )
            settings_summary = build_settings_summary(session=session)

            # ── Phase 3: controls snapshot ──────────────────────────────────
            from autonomous_trading_platform.application.services.platform_replay.controls_hooks import (
                snapshot_controls_at_timestamp,
            )

            snapshot_controls_at_timestamp(
                session=session, timestamp=ctx.timestamp, replay_context=ctx
            )

            # ── Phase 4: safety snapshot ────────────────────────────────────
            from autonomous_trading_platform.application.services.platform_replay.safety_hooks import (
                build_safety_summary,
                snapshot_safety_at_timestamp,
            )

            snapshot_safety_at_timestamp(
                session=session, timestamp=ctx.timestamp, replay_context=ctx
            )

            # ── Phase 5: tick loop ──────────────────────────────────────────
            trading_dates = _trading_dates(inputs.start_date, inputs.end_date)
            governance_counter = 0
            risk_counter = 0

            for tick_date in trading_dates:
                tick_ts = datetime.combine(tick_date, datetime.min.time()).replace(tzinfo=UTC)
                tick_ctx = PlatformReplayContext(
                    run_id=ctx.run_id,
                    replay_id=ctx.replay_id,
                    timestamp=tick_ts,
                    symbols=ctx.symbols,
                    actor=ctx.actor,
                    dry_run=ctx.dry_run,
                    artifact_dir=ctx.artifact_dir,
                )

                tick = TickResult(
                    tick_date=tick_date.isoformat(),
                    tick_timestamp=tick_ts.isoformat(),
                )
                ticks_attempted += 1

                # Apply timeline events scheduled for this date
                for event in _events_for_date(inputs.timeline, tick_date):
                    event_result = _apply_timeline_event(
                        session=session,
                        timestamp=tick_ts,
                        event=event,
                        replay_context=tick_ctx,
                    )
                    tick.timeline_events.append(
                        {
                            "event_type": getattr(event, "event_type", str(event)),
                            "status": event_result.status,
                        }
                    )
                    timeline_events_applied.append(
                        {
                            "date": tick_date.isoformat(),
                            "event_type": getattr(event, "event_type", str(event)),
                            "status": event_result.status,
                        }
                    )

                # Ingestion
                from autonomous_trading_platform.application.services.platform_replay.ingestion_hooks import (
                    run_ingestion_at_timestamp,
                )

                ing_result = run_ingestion_at_timestamp(
                    session=session, timestamp=tick_ts, replay_context=tick_ctx
                )
                tick.ingestion = ing_result.summary
                if ing_result.dataset_version_id:
                    latest_dataset_version_id = ing_result.dataset_version_id
                if ing_result.errors:
                    tick.errors.extend(ing_result.errors)

                # Features
                from autonomous_trading_platform.application.services.platform_replay.features_hooks import (
                    run_features_at_timestamp,
                )

                feat_result = run_features_at_timestamp(
                    session=session,
                    timestamp=tick_ts,
                    dataset_version_id=latest_dataset_version_id,
                    symbols=inputs.symbols,
                    replay_context=tick_ctx,
                )
                tick.features = feat_result.summary
                if feat_result.errors:
                    tick.errors.extend(feat_result.errors)

                # Universe (on rotation day or first tick)
                is_rotation_day = tick_date.weekday() == inputs.universe_rotation_day
                is_first_tick = tick_date == inputs.start_date
                if is_rotation_day or is_first_tick:
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

                # Trading cycle
                from autonomous_trading_platform.application.services.platform_replay.runtime_hooks import (
                    run_trading_cycle_at_timestamp,
                )

                trading_result = run_trading_cycle_at_timestamp(
                    session=session, timestamp=tick_ts, replay_context=tick_ctx
                )
                tick.trading = trading_result.summary
                total_orders += trading_result.orders_submitted
                total_fills += trading_result.fills_received
                if str(tick_ctx.run_id) not in run_ids:
                    run_ids.append(str(tick_ctx.run_id))

                # Risk (every N days)
                risk_counter += 1
                if risk_counter >= inputs.risk_tick_every_n_days:
                    from autonomous_trading_platform.application.services.platform_replay.risk_hooks import (
                        run_risk_at_timestamp,
                    )

                    risk_result = run_risk_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.risk = risk_result.summary
                    risk_counter = 0

                # Governance (every N days)
                governance_counter += 1
                if governance_counter >= inputs.governance_tick_every_n_days:
                    from autonomous_trading_platform.application.services.platform_replay.governance_hooks import (
                        run_governance_at_timestamp,
                    )

                    gov_result = run_governance_at_timestamp(
                        session=session, timestamp=tick_ts, replay_context=tick_ctx
                    )
                    tick.governance = gov_result.summary
                    governance_counter = 0

                # Commit tick
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

                all_warnings.extend(ing_result.warnings + feat_result.warnings)
                tick_results.append({k: v for k, v in tick.__dict__.items()})

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
        """Validate inputs and return intended writes without executing."""
        trading_dates = _trading_dates(inputs.start_date, inputs.end_date)
        rotation_days = [
            d.isoformat() for d in trading_dates if d.weekday() == inputs.universe_rotation_day
        ]
        return {
            "plan": True,
            "dry_run": True,
            "symbols": inputs.symbols,
            "start_date": inputs.start_date.isoformat(),
            "end_date": inputs.end_date.isoformat(),
            "starting_cash": str(inputs.starting_cash),
            "random_seed": inputs.random_seed,
            "cadence_minutes": inputs.cadence_minutes,
            "trading_days": len(trading_dates),
            "universe_rotation_days": rotation_days,
            "governance_tick_every_n_days": inputs.governance_tick_every_n_days,
            "risk_tick_every_n_days": inputs.risk_tick_every_n_days,
            "timeline_events_scheduled": len(inputs.timeline),
            "intended_writes": [
                "ingestion: raw_bars Parquet dataset versions",
                "features: feature Parquet dataset versions",
                "universe: UniverseVersion + RotationRecord (on rotation days)",
                "trading: TrackedOrders, Fills, CashSnapshots, PositionSnapshots, RunManifests",
                "risk: RiskSnapshot, DrawdownGovernanceLadderState",
                "governance: StrategyGovernance, StrategyHealthState",
                "portfolio: implicit in trading snapshots",
            ],
        }


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
    """Return timeline events scheduled on or before tick_date."""
    result: list[Any] = []
    for event in timeline:
        scheduled = getattr(event, "scheduled_date", None)
        if scheduled is None:
            continue
        if isinstance(scheduled, str):
            try:
                scheduled = date.fromisoformat(scheduled)
            except ValueError:
                continue
        if scheduled == tick_date:
            result.append(event)
    return result


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
