"""
Platform replay contracts.

PlatformReplayContext is threaded through every domain hook so each call
carries a consistent run_id, timestamp, actor, and dry_run flag.

DomainReplayResult is the common return shape every hook produces.
Domain-specific result types extend it with typed fields while keeping
the generic summary dict for the artifact bundle.

Timeline event types carry the minimal data the platform runner needs to
dispatch operator-driven changes (safety halts, controls pauses, settings
patches, governance transitions) at a specific point in the replay clock.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Core context — passed to every domain hook
# ---------------------------------------------------------------------------


@dataclass
class PlatformReplayContext:
    """Carries shared state for one platform replay run."""

    run_id: uuid.UUID
    replay_id: str
    timestamp: datetime
    symbols: list[str]
    actor: str
    dry_run: bool
    artifact_dir: Path | None = None

    @classmethod
    def create(
        cls,
        *,
        symbols: list[str],
        timestamp: datetime,
        actor: str = "platform-runner",
        dry_run: bool = False,
        artifact_dir: Path | None = None,
    ) -> PlatformReplayContext:
        return cls(
            run_id=uuid.uuid4(),
            replay_id=f"replay-{uuid.uuid4().hex[:12]}",
            timestamp=timestamp,
            symbols=symbols,
            actor=actor,
            dry_run=dry_run,
            artifact_dir=artifact_dir,
        )


# ---------------------------------------------------------------------------
# Universal domain result — Pydantic so **base unpacking is mypy-clean
# ---------------------------------------------------------------------------


class DomainReplayResult(BaseModel):
    """Common return shape from every domain hook."""

    domain: str
    timestamp: datetime
    run_id: str
    status: Literal["ok", "skipped", "failed", "dry_run"]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "dry_run", "skipped")


# ---------------------------------------------------------------------------
# Domain-specific result types
# ---------------------------------------------------------------------------


class AdminPreflightResult(DomainReplayResult):
    db_ok: bool = True
    config_ok: bool = True
    alembic_version: str | None = None


class IngestionReplayResult(DomainReplayResult):
    dataset_version_id: str | None = None
    symbols_ingested: int = 0
    missing_bar_incident_count: int = 0
    rows_written: int = 0


class FeatureReplayResult(DomainReplayResult):
    feature_version_ids: dict[str, str] = Field(default_factory=dict)
    features_computed: list[str] = Field(default_factory=list)
    features_reused: list[str] = Field(default_factory=list)
    lineage_ok: bool = True


class UniverseReplayResult(DomainReplayResult):
    universe_version_id: str | None = None
    symbol_count: int = 0
    rotation_applied: bool = False
    churn_pct: float | None = None


class TradingCycleReplayResult(DomainReplayResult):
    cycle_run_id: str | None = None
    orders_submitted: int = 0
    fills_received: int = 0
    cycle_blocked: bool = False
    block_reason: str | None = None


class RiskReplayResult(DomainReplayResult):
    risk_snapshot_id: str | None = None
    is_blocked: bool = False
    block_reasons: list[str] = Field(default_factory=list)
    drawdown_pct: float | None = None
    drawdown_ladder_states: dict[str, str] = Field(default_factory=dict)


class GovernanceReplayResult(DomainReplayResult):
    strategies_evaluated: int = 0
    promotions_executed: int = 0
    demotions_executed: int = 0
    health_transitions: int = 0
    strategies_in_breach: list[str] = Field(default_factory=list)


class PortfolioReplayResult(DomainReplayResult):
    portfolio_value: Decimal | None = None
    cash_balance: Decimal | None = None
    open_positions: int = 0


class ControlsReplayResult(DomainReplayResult):
    trading_paused: bool = False
    kill_switch_active: bool = False
    trading_mode: str | None = None
    event_applied: str | None = None


class SettingsReplayResult(DomainReplayResult):
    settings_id: str | None = None
    sha256: str | None = None
    event_applied: str | None = None


class SafetyReplayResult(DomainReplayResult):
    kill_switch_enabled: bool = False
    gate_ok: bool = True
    event_applied: str | None = None


class OperationsReplayResult(DomainReplayResult):
    system_health_status: str | None = None
    active_alerts_count: int = 0
    critical_alerts_count: int = 0


class ExecutionReplayResult(DomainReplayResult):
    open_order_count: int = 0
    fills_this_cycle: int = 0
    adverse_fills: int = 0
    reconciliation_status: str | None = None


class ResearchReplayResult(DomainReplayResult):
    experiment_id: str | None = None
    total_runs: int = 0
    passed_filters: int = 0


class DiagnosticsReplayResult(DomainReplayResult):
    sections_captured: list[str] = Field(default_factory=list)
    snapshot_path: str | None = None


class StrategyCatalogReplayResult(DomainReplayResult):
    total_active: int = 0
    paper_count: int = 0
    live_count: int = 0


# ---------------------------------------------------------------------------
# Domain summary types for the artifact bundle (plain dataclasses — not spread
# with **base, so no need for Pydantic here)
# ---------------------------------------------------------------------------


@dataclass
class AdminPreflightSummary:
    db_ok: bool
    config_ok: bool
    alembic_version: str | None


@dataclass
class IngestionSummary:
    latest_raw_dataset_version_id: str | None
    symbols_covered: int
    missing_bar_incident_count: int
    corporate_actions_ingested: int
    date_range: tuple[str, str] | None


@dataclass
class FeatureSummary:
    feature_versions: dict[str, str]
    lineage_ok: bool
    mixed_lineage_warnings: list[str]


@dataclass
class UniverseSummary:
    active_version_id: str | None
    symbol_count: int
    effective_from: str | None
    rotation_count: int
    last_churn_pct: float | None


@dataclass
class RuntimeSummary:
    ticks_attempted: int
    ticks_ok: int
    ticks_failed: int
    total_orders: int
    total_fills: int
    run_ids: list[str]


@dataclass
class RiskSummary:
    gross_exposure: str | None
    net_exposure: str | None
    drawdown_pct: float | None
    is_blocked: bool
    block_reasons: list[str]
    drawdown_ladder_states: dict[str, str]
    budget_mode: str | None


@dataclass
class GovernanceSummary:
    strategies_evaluated: int
    promotions_executed: int
    demotions_executed: int
    health_transitions: int
    strategies_in_breach: list[str]


@dataclass
class PortfolioSummaryBundle:
    portfolio_value: str | None
    cash_balance: str | None
    invested_capital: str | None
    open_positions: int
    total_pnl_pct: str | None
    equity_curve_points_count: int


@dataclass
class ControlsSummary:
    trading_paused: bool
    kill_switch_active: bool
    trading_mode: str | None
    strategy_count: int
    disabled_strategies: list[str]
    active_overrides: int


@dataclass
class SettingsSummary:
    settings_id: str | None
    sha256: str | None
    risk_tolerance: str | None
    max_drawdown_limit: str | None
    auto_promote_enabled: bool | None
    rebalance_frequency: str | None


@dataclass
class SafetySummary:
    kill_switch_enabled: bool
    live_gate_armed: bool
    trading_environment: str | None
    recent_safety_events: list[str]


@dataclass
class OperationsSummary:
    system_health_status: str | None
    active_alerts_count: int
    critical_alerts_count: int
    jobs_failed_last_cycle: int


@dataclass
class ExecutionSummary:
    open_order_count: int
    fills_this_cycle: int
    adverse_fills: int
    reconciliation_status: str | None
    policy_mode: str | None


@dataclass
class ResearchSummary:
    experiment_id: str | None
    total_runs: int
    passed_filters: int
    run_timestamp: str | None


@dataclass
class DiagnosticsSummary:
    snapshot_timestamp: str | None
    sections_captured: list[str]
    portfolio_value: str | None
    active_strategies: int
    kill_switch_active: bool


@dataclass
class StrategyCatalogSummary:
    total_active: int
    paper_count: int
    live_count: int
    strategy_types: list[str]


# ---------------------------------------------------------------------------
# Timeline event types
# ---------------------------------------------------------------------------


@dataclass
class SafetyTimelineEvent:
    event_type: Literal[
        "safety_emergency_halt",
        "safety_release_halt",
        "live_trading_armed",
        "live_trading_disarmed",
        "safety_startup_check",
    ]
    actor: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_date: str | None = None


@dataclass
class ControlsTimelineEvent:
    event_type: Literal[
        "controls_paused",
        "controls_resumed",
        "trading_enabled",
        "trading_disabled",
        "strategy_disabled",
        "strategy_enabled",
        "allocation_override_set",
        "allocation_override_cleared",
        "trading_mode_changed",
    ]
    actor: str
    reason: str
    strategy_id: str | None = None
    allocation_pct: Decimal | None = None
    trading_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_date: str | None = None


@dataclass
class SettingsTimelineEvent:
    event_type: Literal[
        "settings_changed",
        "settings_seeded",
        "settings_reset_defaults",
    ]
    actor: str
    reason: str
    patch: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_date: str | None = None


@dataclass
class GovernanceTimelineEvent:
    event_type: Literal[
        "governance_manual_transition",
        "governance_auto_promotion",
        "governance_auto_demotion",
        "health_review_acknowledged",
    ]
    actor: str
    reason: str
    strategy_id: str | None = None
    to_state: str | None = None
    actor_role: str = "operator"
    source_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_date: str | None = None


# ---------------------------------------------------------------------------
# Platform artifact bundle
# ---------------------------------------------------------------------------


@dataclass
class PlatformBacktestArtifact:
    """Full output bundle produced by PlatformBacktestRunner."""

    replay_id: str
    run_id: str
    actor: str
    dry_run: bool
    symbols: list[str]
    start_date: str
    end_date: str
    started_at: str
    completed_at: str
    fixture_name: str | None = None
    inject_failures: bool = False

    # Per-domain summaries
    admin: AdminPreflightSummary | None = None
    settings: SettingsSummary | None = None
    controls: ControlsSummary | None = None
    safety: SafetySummary | None = None
    universe: UniverseSummary | None = None
    ingestion: IngestionSummary | None = None
    features: FeatureSummary | None = None
    runtime: RuntimeSummary | None = None
    risk: RiskSummary | None = None
    governance: GovernanceSummary | None = None
    portfolio: PortfolioSummaryBundle | None = None
    operations: OperationsSummary | None = None
    execution: ExecutionSummary | None = None
    research: ResearchSummary | None = None
    diagnostics: DiagnosticsSummary | None = None
    strategy_catalog: StrategyCatalogSummary | None = None

    # Per-tick domain results
    tick_results: list[dict[str, Any]] = field(default_factory=list)

    # Timeline events applied during replay
    timeline_events_applied: list[dict[str, Any]] = field(default_factory=list)

    # Aggregate warnings and errors
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        import dataclasses

        def _convert(obj: Any) -> Any:
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, BaseModel):
                return {k: _convert(v) for k, v in obj.model_dump(mode="json").items()}
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, uuid.UUID):
                return str(obj)
            return obj

        result: dict[str, Any] = _convert(self)
        return result
