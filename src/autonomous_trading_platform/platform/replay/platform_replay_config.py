"""
Platform replay YAML fixture config parser and validator.

Loads a platform_replay fixture YAML, validates the schema, and produces
the typed event list + failure injection schedule needed by the runner.

CLI arg precedence (highest → lowest):
  1. Explicit CLI flags  (--symbols, --start, --end, --starting-cash, --random-seed)
  2. Fixture values      (platform_replay: block)
  3. Hard-coded defaults (100000 cash, seed=42)
"""

from __future__ import annotations

import contextlib
import importlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Allowed value sets
# ---------------------------------------------------------------------------

ALLOWED_JOB_NAMES = frozenset(
    {
        "research",
        "universe",
        "ingestion",
        "corporate_actions",
        "features",
        "trading_cycle",
        "governance",
        "risk",
        "portfolio_snapshot",
        "operations_health",
        "dashboard_snapshot",
    }
)

ALLOWED_CADENCES = frozenset(
    {"daily", "weekly", "biweekly", "monthly", "after_ingestion", "hourly", "per_tick"}
)

CONTROLS_EVENT_TYPES = frozenset(
    {
        "controls_paused",
        "controls_resumed",
        "trading_enabled",
        "trading_disabled",
        "strategy_disabled",
        "strategy_enabled",
        "allocation_override_set",
        "allocation_override_cleared",
        "trading_mode_changed",
    }
)

SETTINGS_EVENT_TYPES = frozenset(
    {
        "settings_changed",
        "settings_seeded",
        "settings_reset_defaults",
    }
)

GOVERNANCE_EVENT_TYPES = frozenset(
    {
        "governance_manual_transition",
        "governance_auto_promotion",
        "governance_auto_demotion",
        "health_review_acknowledged",
    }
)

SAFETY_EVENT_TYPES = frozenset(
    {
        "safety_emergency_halt",
        "safety_release_halt",
        "live_trading_armed",
        "live_trading_disarmed",
        "safety_startup_check",
    }
)

FAILURE_INJECTION_TARGETS = frozenset(
    {
        "ingestion",
        "features",
        "risk",
        "governance",
        "execution",
        "runtime",
    }
)

FAILURE_INJECTION_KINDS = frozenset(
    {
        "missing_bars",
        "late_bars",
        "feature_validation_failure",
        "risk_limit_breach",
        "drawdown_breach",
        "governance_demotion_trigger",
        "broker_reconciliation_mismatch",
        "order_rejected",
        "runtime_job_failure",
    }
)

ALLOWED_TIMELINE_TYPES = (
    CONTROLS_EVENT_TYPES
    | SETTINGS_EVENT_TYPES
    | GOVERNANCE_EVENT_TYPES
    | SAFETY_EVENT_TYPES
    | {"failure_injected"}
)

# Domain hook modules used for availability checks during plan validation
_DOMAIN_HOOK_MODULES: list[tuple[str, str]] = [
    (
        "ingestion",
        "autonomous_trading_platform.application.services.platform_replay.ingestion_hooks",
    ),
    (
        "features",
        "autonomous_trading_platform.application.services.platform_replay.features_hooks",
    ),
    (
        "universe",
        "autonomous_trading_platform.application.services.platform_replay.universe_hooks",
    ),
    (
        "runtime",
        "autonomous_trading_platform.application.services.platform_replay.runtime_hooks",
    ),
    (
        "risk",
        "autonomous_trading_platform.application.services.platform_replay.risk_hooks",
    ),
    (
        "governance",
        "autonomous_trading_platform.application.services.platform_replay.governance_hooks",
    ),
    (
        "portfolio",
        "autonomous_trading_platform.application.services.platform_replay.portfolio_hooks",
    ),
    (
        "settings",
        "autonomous_trading_platform.application.services.platform_replay.settings_hooks",
    ),
    (
        "controls",
        "autonomous_trading_platform.application.services.platform_replay.controls_hooks",
    ),
    (
        "safety",
        "autonomous_trading_platform.application.services.platform_replay.safety_hooks",
    ),
    (
        "research",
        "autonomous_trading_platform.application.services.platform_replay.research_hooks",
    ),
    (
        "operations",
        "autonomous_trading_platform.application.services.platform_replay.operations_hooks",
    ),
    (
        "diagnostics",
        "autonomous_trading_platform.application.services.platform_replay.diagnostics_hooks",
    ),
    (
        "execution",
        "autonomous_trading_platform.application.services.platform_replay.execution_hooks",
    ),
]


# ---------------------------------------------------------------------------
# Pydantic fixture schema
# ---------------------------------------------------------------------------


class ScheduledJobConfig(BaseModel):
    cadence: str = "daily"
    enabled: bool = True

    @field_validator("cadence")
    @classmethod
    def _valid_cadence(cls, v: str) -> str:
        if v not in ALLOWED_CADENCES:
            raise ValueError(f"cadence must be one of {sorted(ALLOWED_CADENCES)}, got {v!r}")
        return v


class TimelineEventConfig(BaseModel):
    at: str
    type: str
    # Controls
    strategy_id: str | None = None
    allocation_pct: Decimal | None = None
    trading_mode: str | None = None
    # Settings
    patch: dict[str, Any] = Field(default_factory=dict)
    # Governance
    to_state: str | None = None
    # Failure injection
    target: str | None = None
    failure: str | None = None
    # Shared
    reason: str = "simulated event"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("at", mode="before")
    @classmethod
    def _coerce_and_valid_date(cls, v: Any) -> str:
        # YAML parses bare dates as datetime.date — coerce to ISO string
        if hasattr(v, "isoformat"):
            return str(v.isoformat())
        try:
            date.fromisoformat(str(v))
        except ValueError as err:
            raise ValueError(f"timeline 'at' must be YYYY-MM-DD, got {v!r}") from err
        return str(v)

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ALLOWED_TIMELINE_TYPES:
            raise ValueError(
                f"Unknown timeline event type: {v!r}. Allowed: {sorted(ALLOWED_TIMELINE_TYPES)}"
            )
        return v


class OutputConfig(BaseModel):
    write_event_log: bool = True
    write_equity_curve: bool = True
    write_portfolio_snapshots: bool = True
    write_governance_audit: bool = True
    write_failure_report: bool = True
    write_dashboard_snapshot: bool = True
    show_progress: bool = True


class InitialStateConfig(BaseModel):
    # Applied by the runner before the first tick via apply_initial_state().
    # Seeds operator settings, strategy governance states, and allocation overrides.
    settings: dict[str, Any] = Field(default_factory=dict)
    controls: dict[str, Any] = Field(default_factory=dict)
    strategies: list[Any] = Field(default_factory=list)
    governance: list[Any] = Field(default_factory=list)
    allocations: list[Any] = Field(default_factory=list)
    universe: dict[str, Any] = Field(default_factory=dict)
    datasets: dict[str, Any] = Field(default_factory=dict)


class PlatformReplayBlock(BaseModel):
    name: str
    mode: Literal["historical_backtest"] = "historical_backtest"
    symbols: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    starting_cash: Decimal | None = None
    random_seed: int | None = None
    cadence_minutes: int = 390  # 390 = daily (one tick per trading day); 5 = 5-min intraday

    @field_validator("symbols")
    @classmethod
    def _upper_symbols(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]

    @field_validator("start", "end", mode="before")
    @classmethod
    def _valid_date(cls, v: Any) -> str | None:
        if v is None:
            return None
        # YAML parses bare dates as datetime.date objects
        if hasattr(v, "isoformat"):
            return str(v.isoformat())
        try:
            date.fromisoformat(str(v))
        except ValueError as err:
            raise ValueError(f"Date must be YYYY-MM-DD, got {v!r}") from err
        return str(v)


class PlatformReplayFixture(BaseModel):
    platform_replay: PlatformReplayBlock
    initial_state: InitialStateConfig = Field(default_factory=InitialStateConfig)
    scheduled_jobs: dict[str, ScheduledJobConfig] = Field(default_factory=dict)
    timeline_events: list[TimelineEventConfig] = Field(default_factory=list)
    outputs: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def _valid_job_names(self) -> PlatformReplayFixture:
        bad = set(self.scheduled_jobs) - ALLOWED_JOB_NAMES
        if bad:
            raise ValueError(
                f"Unknown scheduled job(s): {sorted(bad)}. Allowed: {sorted(ALLOWED_JOB_NAMES)}"
            )
        return self

    @model_validator(mode="after")
    def _valid_failure_injections(self) -> PlatformReplayFixture:
        for ev in self.timeline_events:
            if ev.type != "failure_injected":
                continue
            if ev.target not in FAILURE_INJECTION_TARGETS:
                raise ValueError(
                    f"failure_injected event has unknown target: {ev.target!r}. "
                    f"Allowed: {sorted(FAILURE_INJECTION_TARGETS)}"
                )
            if ev.failure not in FAILURE_INJECTION_KINDS:
                raise ValueError(
                    f"failure_injected event has unknown failure kind: {ev.failure!r}. "
                    f"Allowed: {sorted(FAILURE_INJECTION_KINDS)}"
                )
        return self

    def domain_timeline_events(self) -> list[TimelineEventConfig]:
        return [ev for ev in self.timeline_events if ev.type != "failure_injected"]

    def failure_injections(self) -> list[TimelineEventConfig]:
        return [ev for ev in self.timeline_events if ev.type == "failure_injected"]


# ---------------------------------------------------------------------------
# Load helper
# ---------------------------------------------------------------------------


def load_fixture(path: Path) -> PlatformReplayFixture:
    """Load and validate a platform replay YAML fixture file."""
    import yaml

    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Fixture must be a YAML mapping, got {type(raw).__name__}")
    return cast(PlatformReplayFixture, PlatformReplayFixture.model_validate(raw))


# ---------------------------------------------------------------------------
# Merged params (fixture + CLI overrides)
# ---------------------------------------------------------------------------


@dataclass
class MergedReplayParams:
    """Fully resolved replay parameters after CLI > fixture > default merging."""

    symbols: list[str]
    start_date: date
    end_date: date
    starting_cash: Decimal
    random_seed: int
    cadence_minutes: int = 390
    fixture: PlatformReplayFixture | None = None
    scheduled_jobs: dict[str, ScheduledJobConfig] = field(default_factory=dict)
    timeline_events: list[TimelineEventConfig] = field(default_factory=list)
    failure_injections: list[TimelineEventConfig] = field(default_factory=list)
    outputs: OutputConfig = field(default_factory=OutputConfig)


def merge_fixture_with_cli(
    *,
    fixture: PlatformReplayFixture | None = None,
    cli_symbols: list[str] | None = None,
    cli_start: str | None = None,
    cli_end: str | None = None,
    cli_starting_cash: Decimal | None = None,
    cli_random_seed: int | None = None,
    cli_cadence_minutes: int | None = None,
) -> MergedReplayParams:
    """Merge fixture + CLI overrides. Raises ValueError on missing required fields."""
    cfg = fixture.platform_replay if fixture else None

    symbols = cli_symbols or (cfg.symbols if cfg else [])
    if not symbols:
        raise ValueError("--symbols is required (or specify symbols in fixture)")

    raw_start = cli_start or (cfg.start if cfg else None)
    raw_end = cli_end or (cfg.end if cfg else None)
    if not raw_start:
        raise ValueError("--start is required (or specify start in fixture)")
    if not raw_end:
        raise ValueError("--end is required (or specify end in fixture)")

    start_date = date.fromisoformat(raw_start)
    end_date = date.fromisoformat(raw_end)
    if start_date >= end_date:
        raise ValueError(f"start ({start_date}) must be before end ({end_date})")

    starting_cash = cli_starting_cash or (cfg.starting_cash if cfg else None) or Decimal("100000")
    random_seed = (
        cli_random_seed
        if cli_random_seed is not None
        else ((cfg.random_seed if cfg else None) or 42)
    )
    # CLI takes precedence over fixture; fixture takes precedence over default
    cadence_minutes = (
        cli_cadence_minutes
        if cli_cadence_minutes is not None
        else (cfg.cadence_minutes if cfg else 390)
    )

    return MergedReplayParams(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        starting_cash=starting_cash,
        random_seed=random_seed,
        cadence_minutes=cadence_minutes,
        fixture=fixture,
        scheduled_jobs=fixture.scheduled_jobs if fixture else {},
        timeline_events=fixture.domain_timeline_events() if fixture else [],
        failure_injections=fixture.failure_injections() if fixture else [],
        outputs=fixture.outputs if fixture else OutputConfig(),
    )


# ---------------------------------------------------------------------------
# Typed event builder (fixture → runtime contracts)
# ---------------------------------------------------------------------------


def build_typed_timeline_events(events: list[TimelineEventConfig], actor: str) -> list[Any]:
    """Convert raw fixture TimelineEventConfig list to typed event dataclasses."""
    from autonomous_trading_platform.contracts.runtime.platform_replay import (
        ControlsTimelineEvent,
        GovernanceTimelineEvent,
        SafetyTimelineEvent,
        SettingsTimelineEvent,
    )

    result: list[Any] = []
    for ev in sorted(events, key=lambda e: e.at):
        t = ev.type
        if t in CONTROLS_EVENT_TYPES:
            result.append(
                ControlsTimelineEvent(
                    event_type=t,  # type: ignore[arg-type]
                    actor=actor,
                    reason=ev.reason,
                    strategy_id=ev.strategy_id,
                    allocation_pct=ev.allocation_pct,
                    trading_mode=ev.trading_mode,
                    metadata=ev.metadata,
                    scheduled_date=ev.at,
                )
            )
        elif t in SETTINGS_EVENT_TYPES:
            result.append(
                SettingsTimelineEvent(
                    event_type=t,  # type: ignore[arg-type]
                    actor=actor,
                    reason=ev.reason,
                    patch=ev.patch,
                    metadata=ev.metadata,
                    scheduled_date=ev.at,
                )
            )
        elif t in GOVERNANCE_EVENT_TYPES:
            result.append(
                GovernanceTimelineEvent(
                    event_type=t,  # type: ignore[arg-type]
                    actor=actor,
                    reason=ev.reason,
                    strategy_id=ev.strategy_id,
                    to_state=ev.to_state,
                    metadata=ev.metadata,
                    scheduled_date=ev.at,
                )
            )
        elif t in SAFETY_EVENT_TYPES:
            result.append(
                SafetyTimelineEvent(
                    event_type=t,  # type: ignore[arg-type]
                    actor=actor,
                    reason=ev.reason,
                    metadata=ev.metadata,
                    scheduled_date=ev.at,
                )
            )
    return result


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_plan(
    params: MergedReplayParams,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Validate merged replay params for the plan command.

    Returns a structured plan dict with:
      - valid / issues / warnings
      - trading_days count, rotation_days
      - scheduled_jobs config
      - timeline_events schedule
      - failure_injections schedule
      - domain_handler_status (import-availability check per domain)
    """
    issues: list[str] = []
    warnings: list[str] = []

    # Date range sanity
    trading_days = _count_trading_days(params.start_date, params.end_date)
    if trading_days == 0:
        issues.append(
            f"Date range {params.start_date} → {params.end_date} contains no trading days"
        )

    rotation_days = [
        (params.start_date + timedelta(days=i)).isoformat()
        for i in range((params.end_date - params.start_date).days + 1)
        if (params.start_date + timedelta(days=i)).weekday() == 0  # Monday
        and (params.start_date + timedelta(days=i)).weekday() < 5
    ]

    # Symbol sanity
    if not params.symbols:
        issues.append("symbols list is empty")

    # Output path
    if output_path and not output_path.parent.exists():
        warnings.append(
            f"Output directory does not exist and will be created: {output_path.parent}"
        )

    # Domain handler availability
    domain_handler_status: dict[str, str] = {}
    for domain_name, module_path in _DOMAIN_HOOK_MODULES:
        try:
            importlib.import_module(module_path)
            domain_handler_status[domain_name] = "available"
        except ImportError as exc:
            domain_handler_status[domain_name] = f"unavailable: {exc}"
            warnings.append(f"Domain handler not importable: {domain_name} — {exc}")

    # Timeline event date bounds
    for ev in params.timeline_events:
        ev_date = date.fromisoformat(ev.at)
        if ev_date < params.start_date or ev_date > params.end_date:
            warnings.append(
                f"Timeline event '{ev.type}' at {ev.at} is outside replay range "
                f"[{params.start_date}, {params.end_date}] — it will be ignored"
            )

    for ev in params.failure_injections:
        ev_date = date.fromisoformat(ev.at)
        if ev_date < params.start_date or ev_date > params.end_date:
            warnings.append(
                f"Failure injection '{ev.failure}' at {ev.at} is outside replay range — it will be ignored"
            )

    # Scheduled job config summary — handle both ScheduledJobConfig and plain dicts
    def _job_info(cfg: Any) -> dict[str, Any]:
        if isinstance(cfg, dict):
            return {"cadence": cfg.get("cadence", "daily"), "enabled": cfg.get("enabled", True)}
        return {
            "cadence": getattr(cfg, "cadence", "daily"),
            "enabled": getattr(cfg, "enabled", True),
        }

    scheduled_jobs_plan = {name: _job_info(cfg) for name, cfg in params.scheduled_jobs.items()}

    return {
        "plan": True,
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "fixture_name": params.fixture.platform_replay.name if params.fixture else None,
        "symbols": params.symbols,
        "start_date": params.start_date.isoformat(),
        "end_date": params.end_date.isoformat(),
        "starting_cash": str(params.starting_cash),
        "random_seed": params.random_seed,
        "trading_days": trading_days,
        "universe_rotation_days": rotation_days,
        "scheduled_jobs": scheduled_jobs_plan,
        "timeline_events_scheduled": [
            {"at": ev.at, "type": ev.type, "strategy_id": ev.strategy_id}
            for ev in sorted(params.timeline_events, key=lambda e: e.at)
        ],
        "failure_injections_scheduled": [
            {"at": ev.at, "target": ev.target, "failure": ev.failure}
            for ev in sorted(params.failure_injections, key=lambda e: e.at)
        ],
        "domain_handler_status": domain_handler_status,
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
# Failure injection dispatch (called by the runner when inject_failures=True)
# ---------------------------------------------------------------------------


def dispatch_failure_injection(
    *,
    session: Any,
    event: TimelineEventConfig,
    timestamp: Any,
    symbols: list[str],
    replay_run_id: str,
    dataset_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Dispatch a single failure injection event to the appropriate hook.

    Returns a result dict with target, failure, status, and details.
    """
    from autonomous_trading_platform.application.services.platform_replay.failure_injection import (
        inject_broker_reconciliation_mismatch,
        inject_drawdown_breach,
        inject_feature_validation_failure,
        inject_governance_demotion_trigger,
        inject_ingestion_late_bars,
        inject_ingestion_missing_bars,
        inject_order_rejected,
        inject_risk_limit_breach,
        inject_runtime_job_failure,
    )

    target = event.target or ""
    failure = event.failure or ""
    result: dict[str, Any] = {
        "at": event.at,
        "target": target,
        "failure": failure,
        "status": "skipped",
        "detail": None,
    }

    try:
        if failure == "missing_bars":
            ids = inject_ingestion_missing_bars(
                session=session,
                timestamp=timestamp,
                symbols=symbols,
                dataset_version=dataset_version or "unknown",
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"incident_ids": ids})

        elif failure == "late_bars":
            ids = inject_ingestion_late_bars(
                session=session,
                timestamp=timestamp,
                symbols=symbols,
                dataset_version=dataset_version or "unknown",
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"incident_ids": ids})

        elif failure == "feature_validation_failure":
            ok = inject_feature_validation_failure(
                session=session,
                feature_dataset_version_id=dataset_version or "unknown",
                replay_run_id=replay_run_id,
            )
            result.update(status="injected" if ok else "skipped_no_row")

        elif failure == "risk_limit_breach":
            snap_id = inject_risk_limit_breach(
                session=session,
                run_id=run_id or replay_run_id,
                timestamp=timestamp,
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"snapshot_id": snap_id})

        elif failure == "drawdown_breach" and symbols:
            ladder_id = inject_drawdown_breach(
                session=session,
                strategy_id=symbols[0],
                realized_drawdown=0.25,
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"ladder_state_id": ladder_id})

        elif failure == "governance_demotion_trigger" and symbols:
            metrics_id = inject_governance_demotion_trigger(
                session=session,
                strategy_id=symbols[0],
                replay_run_id=replay_run_id,
            )
            if metrics_id is None:
                result.update(
                    status="skipped_no_data",
                    detail="No simulation_runs row found for strategy — seed a simulation before injecting governance demotion",
                )
            else:
                result.update(status="injected", detail={"metrics_id": metrics_id})

        elif failure == "broker_reconciliation_mismatch" and symbols and run_id:
            event_id = inject_broker_reconciliation_mismatch(
                session=session,
                run_id=run_id,
                symbol=symbols[0],
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"audit_event_id": event_id})

        elif failure == "order_rejected" and symbols and run_id:
            event_id = inject_order_rejected(
                session=session,
                run_id=run_id,
                symbol=symbols[0],
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"audit_event_id": event_id})

        elif failure == "runtime_job_failure":
            job_run_id = inject_runtime_job_failure(
                session=session,
                job_name=target,
                timestamp=timestamp,
                replay_run_id=replay_run_id,
            )
            result.update(status="injected", detail={"job_run_id": job_run_id})

        else:
            result["status"] = "unsupported"
            result["detail"] = f"No injection handler for {target}/{failure}"

    except Exception as exc:
        # Rollback the session to clear any failed flush so the runner can continue.
        # Without this, a FK violation during session.flush() leaves the session in a
        # PendingRollbackError state that crashes the entire runner tick loop.
        with contextlib.suppress(Exception):
            session.rollback()
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_trading_days(start: date, end: date) -> int:
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count
