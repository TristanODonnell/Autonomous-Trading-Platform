# autonomous_trading_platform/scheduler/common/trading_cycle_common.py

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    ExecutionContext,
    build_execution_context,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.safety.contexts.build_safety_context import (
    SafetyContext,
    build_safety_context,
)
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import StubOrderActivityReader
from autonomous_trading_platform.safety.readers.risk_state_reader import (
    PositionAwareRiskStateReader,
)
from autonomous_trading_platform.storage.parquet.reader import MemoizingBarDatasetReader
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.strategy.configs.stored_config import stored_config_parameters
from autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context import (
    StrategyRuntimeContext,
    build_strategy_runtime_context,
)
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from autonomous_trading_platform.universe.services.universe_resolution_service import (
    UniverseResolutionService,
)

logger = get_logger(__name__)

# DB state strings used throughout the codebase (long form)
_PAPER_DB_STATE = "approved_for_paper_trading"
_LIVE_DB_STATE = "approved_for_live_trading"
_DB_STATE_TO_GOVERNANCE: dict[str, GovernanceState] = {
    _PAPER_DB_STATE: GovernanceState.APPROVED_PAPER,
    _LIVE_DB_STATE: GovernanceState.APPROVED_LIVE,
}


@dataclass(slots=True)
class TradingCycleWindow:
    now_utc: datetime
    cycle_start: datetime
    cycle_end: datetime
    ingestion_deadline: datetime


@dataclass(slots=True)
class StrategyRuntime:
    """One strategy the trading cycle runs in portfolio mode.

    ACTIVE and WINDING_DOWN trade real sleeves; ON_DECK is shadow-traded only.
    """

    strategy_id: str
    status: MembershipStatus
    # Governance state used for sizing. ON_DECK runtimes are sized under the
    # approval they would trade with if promoted (paper, or live in live mode).
    governance_state: GovernanceState
    # Fraction of total capital; 0 for WINDING_DOWN (exit-only) runtimes, and a
    # notional share (no capital reserved) for ON_DECK runtimes.
    budget_pct: Decimal
    # None for WINDING_DOWN runtimes, which never evaluate signals.
    strategy_context: StrategyRuntimeContext | None = None


PORTFOLIO_STRATEGY_ID = "portfolio"


@dataclass(slots=True)
class TradingCycleDependencies:
    session: Session
    settings: Settings
    audit_logger: AuditLoggingService
    manifest_service: RunManifestService
    strategy_context: StrategyRuntimeContext
    safety_context: SafetyContext
    execution_context: ExecutionContext
    portfolio_engine: IAllocationProvider
    active_strategy_id: str
    active_governance_state: GovernanceState
    # Portfolio mode: one runtime per active / winding-down strategy. None means the
    # legacy single-strategy path (no active set yet) driven by the fields above.
    strategy_runtimes: list[StrategyRuntime] | None = None


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def build_trading_cycle_window(
    now_utc: datetime | None = None,
    ingestion_grace_seconds: int = 60,
) -> TradingCycleWindow:
    resolved_now = now_utc or datetime.now(UTC)
    cycle_end = floor_to_five_minutes(resolved_now)
    cycle_start = cycle_end - timedelta(minutes=5)
    ingestion_deadline = cycle_end + timedelta(seconds=ingestion_grace_seconds)

    return TradingCycleWindow(
        now_utc=resolved_now,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        ingestion_deadline=ingestion_deadline,
    )


def _build_portfolio_engine(session: Session, settings: Settings) -> PortfolioEngine:
    """
    Construct PortfolioEngine with its three repositories.
    initial_capital from settings is the starting total_capital;
    it gets synced to real broker equity at the start of each cycle.
    """
    return PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(session),
        overrides_repo=AllocationOverridesRepository(session),
        promotion_rules_repo=PromotionRulesRepository(session),
        total_capital=float(settings.initial_capital),
    )


def _resolve_active_strategy(
    session: Session,
    settings: Settings,
) -> tuple[BaseStrategy, str, GovernanceState, int]:
    """
    Query governance for the approved strategy appropriate to the current trading
    environment. Returns (strategy_instance, strategy_id, governance_state, warmup_bars).

    Tries each approved strategy in order (most recently updated first, then
    alphabetically for stable tiebreaking). Attempts instantiation via the
    StrategyRegistry; if the type is unknown, falls back to a StubStrategy that
    carries the correct strategy_id so runtime state and signal attribution stay
    consistent with the governance entry.

    Falls back to (StubStrategy(), "baseline_strategy", APPROVED_PAPER, 1) when no
    approved strategy rows exist.
    """
    from sqlalchemy import or_ as _sa_or
    from sqlalchemy import select as _sa_select

    from autonomous_trading_platform.strategy.registry import get_registry

    if settings.trading_environment is TradingEnvironment.LIVE:
        # Live environment: only pick strategies explicitly approved for live.
        state_filter = StrategyGovernance.current_state == _LIVE_DB_STATE
        fallback_governance_state = GovernanceState.APPROVED_LIVE
    else:
        # Paper / backtest: include live-approved strategies too.
        # Strategies that graduate to approved_for_live_trading are the best
        # performers; in a backtest there is no separate live broker, so they
        # should keep executing through the simulated broker rather than falling
        # out of the active pool.
        state_filter = _sa_or(
            StrategyGovernance.current_state == _PAPER_DB_STATE,
            StrategyGovernance.current_state == _LIVE_DB_STATE,
        )
        fallback_governance_state = GovernanceState.APPROVED_PAPER

    gov_rows = list(
        session.scalars(
            _sa_select(StrategyGovernance)
            .where(state_filter)
            .order_by(
                StrategyGovernance.updated_at.desc(),
                StrategyGovernance.strategy_id.asc(),
            )
        ).all()
    )

    if not gov_rows:
        logger.warning(
            "trading_cycle.no_approved_strategy_found",
            extra={
                "target_env": settings.trading_environment.value,
                "live_included": settings.trading_environment is not TradingEnvironment.LIVE,
            },
        )
        return StubStrategy(), "baseline_strategy", GovernanceState.APPROVED_PAPER, 1

    registry = get_registry()
    for gov_row in gov_rows:
        # Derive governance state from the actual row — a graduating strategy
        # may be in approved_for_live_trading while the cycle is paper mode.
        row_governance_state = _DB_STATE_TO_GOVERNANCE.get(
            gov_row.current_state, fallback_governance_state
        )
        config_row = session.get(StrategyConfigs, gov_row.strategy_id)
        if config_row is None:
            continue
        try:
            defn = registry.get_definition(config_row.strategy_type)
            # Unwrap research's {type, parameters} config, as _instantiate_strategy does,
            # so a researched strategy trades with its own parameters here too.
            params = {
                **(defn.default_parameters or {}),
                **stored_config_parameters(config_row.config_json),
            }
            strategy = defn.builder(strategy_id=gov_row.strategy_id, params=params)
            warmup_bars = max(defn.warmup_bars_fn(params), 1)
            logger.info(
                "trading_cycle.active_strategy_resolved",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                    "governance_state": gov_row.current_state,
                    "warmup_bars": warmup_bars,
                },
            )
            return strategy, gov_row.strategy_id, row_governance_state, warmup_bars
        except KeyError:
            logger.warning(
                "trading_cycle.strategy_type_not_registered_using_stub",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                },
            )
            return (
                StubStrategy(strategy_id=gov_row.strategy_id),
                gov_row.strategy_id,
                row_governance_state,
                1,
            )
        except Exception as exc:
            logger.warning(
                "trading_cycle.strategy_instantiation_failed_using_stub",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                    "error": str(exc),
                },
            )
            return (
                StubStrategy(strategy_id=gov_row.strategy_id),
                gov_row.strategy_id,
                row_governance_state,
                1,
            )

    # All rows lacked configs
    logger.warning(
        "trading_cycle.no_strategy_config_found_using_stub",
        extra={
            "fallback_state": str(fallback_governance_state),
            "governance_row_count": len(gov_rows),
        },
    )
    return StubStrategy(), "baseline_strategy", GovernanceState.APPROVED_PAPER, 1


def _instantiate_strategy(session: Session, strategy_id: str) -> tuple[BaseStrategy, int]:
    """Build a strategy instance from its config via the StrategyRegistry.

    Research stores configs wrapped as {type, parameters, strategy_id}; hand-seeded
    configs are the bare parameter dict. Either way the parameters are validated and
    default-filled exactly as the research StrategyFactory does, so a strategy trades
    with the parameters it was researched (and approved) with.

    Falls back to a StubStrategy carrying the real strategy_id (so runtime state and
    attribution stay consistent) when the config or registry entry is missing or invalid.
    """
    from autonomous_trading_platform.strategy.registry import get_registry

    config_row = session.get(StrategyConfigs, strategy_id)
    if config_row is None:
        logger.warning("trading_cycle.strategy_config_missing", extra={"strategy_id": strategy_id})
        return StubStrategy(strategy_id=strategy_id), 1
    try:
        defn = get_registry().get_definition(config_row.strategy_type)
        params = defn.normalize_parameters(stored_config_parameters(config_row.config_json))
        # Same bar count as research re-sims (StrategyDefinition.context_lookback_bars).
        return defn.builder(strategy_id=strategy_id, params=params), defn.context_lookback_bars(
            params
        )
    except Exception as exc:
        logger.warning(
            "trading_cycle.strategy_instantiation_failed_using_stub",
            extra={
                "strategy_id": strategy_id,
                "strategy_type": config_row.strategy_type,
                "error": str(exc),
            },
        )
        return StubStrategy(strategy_id=strategy_id), 1


def _latest_governance_state(session: Session, strategy_id: str) -> GovernanceState:
    from sqlalchemy import select as _sa_select

    row = session.scalars(
        _sa_select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == strategy_id)
        .order_by(StrategyGovernance.updated_at.desc())
        .limit(1)
    ).one_or_none()
    # Winding-down members may already be demoted; they only sell, so paper is safe.
    if row is None:
        return GovernanceState.APPROVED_PAPER
    return _DB_STATE_TO_GOVERNANCE.get(row.current_state, GovernanceState.APPROVED_PAPER)


def resolve_strategy_runtimes(
    *,
    session: Session,
    settings: Settings,
    dataset_version_id_override: str | None = None,
    now_utc: datetime | None = None,
) -> list[StrategyRuntime] | None:
    """Refresh the active portfolio set and build a runtime per trading member.

    Returns None — keeping the legacy single-strategy path — when portfolio mode is
    off (operator_settings.portfolio_mode_enabled) or there are no trading or
    on-deck members. ON_DECK runtimes come after the trading ones.
    """
    operator_settings = OperatorSettingsRepository(session).get_or_create_default()
    if not operator_settings.portfolio_mode_enabled:
        return None
    service = ActivePortfolioService(session, trading_environment=settings.trading_environment)
    service.refresh(now=now_utc)
    members = service.trading_members()
    on_deck = service.on_deck_members()
    if not members and not on_deck:
        return None

    budgets = {budget.strategy_id: budget.pct_of_capital for budget in service.budgets(now=now_utc)}
    # One reader for the whole cycle: every strategy reads the same symbol windows.
    bar_reader = MemoizingBarDatasetReader(session=session, base_path="data")
    runtimes: list[StrategyRuntime] = []
    for member in members:
        context = None
        if member.status == MembershipStatus.ACTIVE:
            strategy, warmup_bars = _instantiate_strategy(session, member.strategy_id)
            context = build_strategy_runtime_context(
                session=session,
                strategy=strategy,
                dataset_version=dataset_version_id_override,
                lookback_bars=warmup_bars,
                bar_reader=bar_reader,
            )
        runtimes.append(
            StrategyRuntime(
                strategy_id=member.strategy_id,
                status=member.status,
                governance_state=_latest_governance_state(session, member.strategy_id),
                budget_pct=budgets.get(member.strategy_id, Decimal("0")),
                strategy_context=context,
            )
        )

    if on_deck:
        on_deck_budget = service.on_deck_budget_pct()
        sizing_state = (
            GovernanceState.APPROVED_LIVE
            if settings.trading_environment is TradingEnvironment.LIVE
            else GovernanceState.APPROVED_PAPER
        )
        for member in on_deck:
            strategy, warmup_bars = _instantiate_strategy(session, member.strategy_id)
            runtimes.append(
                StrategyRuntime(
                    strategy_id=member.strategy_id,
                    status=MembershipStatus.ON_DECK,
                    governance_state=sizing_state,
                    budget_pct=on_deck_budget,
                    strategy_context=build_strategy_runtime_context(
                        session=session,
                        strategy=strategy,
                        dataset_version=dataset_version_id_override,
                        lookback_bars=warmup_bars,
                        bar_reader=bar_reader,
                    ),
                )
            )
    logger.info(
        "trading_cycle.portfolio_runtimes_resolved",
        extra={
            "active": [r.strategy_id for r in runtimes if r.status == MembershipStatus.ACTIVE],
            "winding_down": [
                r.strategy_id for r in runtimes if r.status == MembershipStatus.WINDING_DOWN
            ],
            "on_deck": [r.strategy_id for r in runtimes if r.status == MembershipStatus.ON_DECK],
            "budgets": {r.strategy_id: str(r.budget_pct) for r in runtimes},
        },
    )
    return runtimes


def build_trading_cycle_dependencies(
    broker_client: object | None = None,
    dataset_version_id_override: str | None = None,
    resolve_strategies: bool = True,
    now_utc: datetime | None = None,
) -> TradingCycleDependencies:
    """Build everything a trading cycle needs.

    resolve_strategies=False skips the active-set refresh and strategy construction
    for callers that only need the execution context (e.g. order reconciliation).
    """
    settings = Settings()
    session = get_session()
    audit_logger = AuditLoggingService(session)
    audit_log_repository = AuditLogRepository(session)
    manifest_service = RunManifestService(session)

    environment_safety_policy = EnvironmentSafetyPolicy(settings=settings)

    strategy_runtimes: list[StrategyRuntime] | None = None
    if resolve_strategies:
        strategy_runtimes = resolve_strategy_runtimes(
            session=session,
            settings=settings,
            dataset_version_id_override=dataset_version_id_override,
            now_utc=now_utc,
        )

    active_strategy: BaseStrategy
    if not resolve_strategies:
        active_strategy = StubStrategy()
        active_strategy_id = "baseline_strategy"
        active_governance_state = GovernanceState.APPROVED_PAPER
        warmup_bars = 1
    elif strategy_runtimes is None:
        active_strategy, active_strategy_id, active_governance_state, warmup_bars = (
            _resolve_active_strategy(session=session, settings=settings)
        )
    else:
        active_strategy = StubStrategy(strategy_id=PORTFOLIO_STRATEGY_ID)
        active_strategy_id = PORTFOLIO_STRATEGY_ID
        active_governance_state = (
            GovernanceState.APPROVED_LIVE
            if settings.trading_environment is TradingEnvironment.LIVE
            else GovernanceState.APPROVED_PAPER
        )
        warmup_bars = 1

    first_active_context = next(
        (
            r.strategy_context
            for r in strategy_runtimes or []
            if r.strategy_context is not None and r.status == MembershipStatus.ACTIVE
        ),
        None,
    )
    strategy_context = first_active_context or build_strategy_runtime_context(
        session=session,
        strategy=active_strategy,
        dataset_version=dataset_version_id_override,
        lookback_bars=warmup_bars,
    )

    # Real symbol-level state (positions/exposure) so risk-reducing sells pass and the
    # per-symbol cap reflects actual holdings; aggregate limits keep per-order semantics.
    risk_state_reader = PositionAwareRiskStateReader.from_session(session)
    order_activity_reader = StubOrderActivityReader()

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_safety_policy,
        risk_state_reader=risk_state_reader,
        order_activity_reader=order_activity_reader,
        audit_log_repository=audit_log_repository,
        session=session,
    )

    portfolio_engine = _build_portfolio_engine(session=session, settings=settings)
    if strategy_runtimes is not None:
        portfolio_engine.set_cycle_budgets(
            {runtime.strategy_id: float(runtime.budget_pct) for runtime in strategy_runtimes}
        )

    execution_context = build_execution_context(
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_log_repository,
        alpaca_settings=settings,
        portfolio_engine=portfolio_engine,
        session=session,
        broker_client=broker_client,
    )

    return TradingCycleDependencies(
        session=session,
        settings=settings,
        audit_logger=audit_logger,
        manifest_service=manifest_service,
        strategy_context=strategy_context,
        safety_context=safety_context,
        execution_context=execution_context,
        portfolio_engine=portfolio_engine,
        active_strategy_id=active_strategy_id,
        active_governance_state=active_governance_state,
        strategy_runtimes=strategy_runtimes,
    )


def build_trading_run_manifest(
    *,
    run_id,
    now_utc: datetime,
    cycle_start: datetime,
    cycle_end: datetime,
    trading_environment: TradingEnvironment = TradingEnvironment.PAPER,
    created_at: datetime | None = None,
    universe_version_id: str | None = None,
    universe_source: str | None = None,
    universe_member_count: int | None = None,
    strategy_id: str | None = None,
    governance_state: GovernanceState | None = None,
) -> RunManifest:
    run_type = RunType.LIVE if trading_environment is TradingEnvironment.LIVE else RunType.PAPER
    resolved_governance_state = governance_state or GovernanceState.APPROVED_PAPER
    return RunManifest(
        run_id=run_id,
        run_type=run_type,
        created_at=created_at or datetime.now(UTC),
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id=strategy_id or "baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.FIVE_MIN,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version=universe_version_id or "v1",
        universe_version_id=universe_version_id,
        universe_source=universe_source,
        universe_member_count=universe_member_count,
        git_commit="dev",
        python_version=platform.python_version(),
        notes="5-minute trading cycle",
        price_basis=PriceBasis.ADJUSTED,
        governance_state=resolved_governance_state,
    )


def resolve_trading_universe(
    session,
    now_utc: datetime,
) -> tuple[set[str], str | None, str | None, int | None]:
    """
    Resolve the active universe for a trading cycle.

    Returns (symbols, universe_version_id, universe_source, member_count).
    Raises ``NoActiveUniverseError`` if no active universe exists.
    """

    resolution_service = UniverseResolutionService(UniverseVersionRepository(session))
    resolution_service.assert_active_universe_exists(now_utc)
    active_version = resolution_service.resolve_active(now_utc)
    members = _drop_delisted(session, resolution_service.resolve_active_members(now_utc), now_utc)
    return (
        set(members),
        active_version.universe_version_id,
        active_version.source,
        len(members),
    )


def _drop_delisted(session, members: list[str], now_utc: datetime) -> list[str]:
    """Remove members with a DELISTING lifecycle event effective by now_utc.

    A universe version is fixed until the next rotation, but a member can stop
    trading in between; without this the strategy keeps signalling on it.
    """
    from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
        TickerLifecycleRepository,
    )
    from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
        TickerLifecycleService,
    )

    lifecycle = TickerLifecycleService(TickerLifecycleRepository(session))
    kept = [m for m in members if not lifecycle.is_delisted(m, now_utc)]
    dropped = sorted(set(members) - set(kept))
    if dropped:
        logger.info(
            "trading_universe.delisted_members_dropped",
            extra={"symbols": dropped, "as_of": now_utc.isoformat()},
        )
    return kept


def build_trading_base_metadata(
    *,
    cycle_start: datetime,
    cycle_end: datetime,
    expected_symbols: set[str],
    manifest: RunManifest,
) -> dict[str, object]:
    return {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "expected_symbols": sorted(expected_symbols),
        "manifest_run_type": manifest.run_type.value,
        "manifest_interval": manifest.interval.value,
    }


def new_trading_run_id():
    return uuid.uuid4()
