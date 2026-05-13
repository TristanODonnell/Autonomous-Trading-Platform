from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    OrderSource,
    OrderStatus,
    OrderType,
    PriceBasis,
    Side,
    SignalDirection,
    TimeInForce,
)
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.runtime.clock import (
    HistoricalMarketCalendar,
    HistoricalTradingClock,
    MarketCalendar,
    TradingClock,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.operator_settings import (
    OperatorSettingsRow,
)
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.signals import Signal
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

DEBUG_REPLAY_SOURCE = "runtime_replay_debug"
DEBUG_REPLAY_RUN_TYPE = "DEBUG_REPLAY"
SUPPORTED_CYCLES = {
    "market_backfill",
    "market_ingestion",
    "features",
    "trading",
    "rebalance",
    "portfolio_snapshot",
    "corporate_actions",
    "eod",
    "runtime_checks",
}
FULL_RUNTIME_DAY = [
    "market_backfill",
    "features",
    "trading",
    "rebalance",
    "portfolio_snapshot",
]
DEFAULT_STRATEGY_ID = "baseline_strategy"


class RuntimeReplaySettings(Protocol):
    @property
    def app_env(self) -> str: ...

    @property
    def trading_environment(self) -> TradingEnvironment: ...

    @property
    def no_live_trading(self) -> bool: ...

    @property
    def shadow_mode_enabled(self) -> bool: ...

    @property
    def max_gross_exposure(self) -> float: ...

    @property
    def max_net_exposure(self) -> float: ...

    @property
    def max_symbol_exposure(self) -> float: ...

    @property
    def max_leverage(self) -> float: ...

    @property
    def max_orders_per_hour(self) -> int: ...

    @property
    def max_orders_per_bar(self) -> int: ...

    @property
    def max_daily_notional_traded(self) -> float: ...

    @property
    def max_reserved_cash(self) -> float: ...


@dataclass(frozen=True)
class RuntimeReplayInputs:
    symbols: list[str]
    start_date: date
    end_date: date
    starting_cash: Decimal
    random_seed: int
    price_basis: PriceBasis
    calendar_mode: str
    cycles: list[str]
    reset_sim_state: bool = False
    print_summary: bool = True
    output_json: Path | None = None
    cadence_minutes: int = 390
    max_ticks: int | None = None


@dataclass
class ReplayCounters:
    cycle_invocations: Counter[str] = field(default_factory=Counter)
    runtime_jobs_completed: int = 0
    runtime_jobs_failed: int = 0
    trading_days_processed: set[date] = field(default_factory=set)
    signals_generated: int = 0
    orders_attempted: int = 0
    orders_submitted: int = 0
    orders_blocked_by_risk: int = 0
    fills_created: int = 0
    rebalance_checks: int = 0
    rebalances_triggered: int = 0
    rebalances_skipped: int = 0
    rebalance_skip_reasons: Counter[str] = field(default_factory=Counter)
    portfolio_snapshots: int = 0
    market_data_points_seen: int = 0


@dataclass
class ReplayExecutionState:
    cash: Decimal
    positions: dict[str, Decimal] = field(default_factory=dict)
    avg_costs: dict[str, Decimal] = field(default_factory=dict)
    current_prices: dict[str, Decimal] = field(default_factory=dict)
    peak_equity: Decimal = Decimal("0")
    latest_equity: Decimal = Decimal("0")
    last_rebalance_date: date | None = None
    tick_index: int = 0


@dataclass
class ReplayCycleContext:
    session: Session
    inputs: RuntimeReplayInputs
    replay_id: str
    replay_uuid: uuid.UUID
    clock: TradingClock
    settings_snapshot: dict[str, Any]
    settings_snapshot_hash: str
    counters: ReplayCounters
    state: ReplayExecutionState
    warnings: list[str]


class _SessionRuntimeJobRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, contract: RuntimeJobRun) -> RuntimeJobRun:
        repo = RuntimeJobRunRepository(self._session)
        row = repo.to_row(contract)
        saved = repo.upsert(row)
        self._session.flush()
        return repo.to_contract(saved)


CycleHandler = Callable[[ReplayCycleContext], dict[str, Any] | None]


class RuntimeReplayDebugRunner:
    def __init__(
        self,
        *,
        inputs: RuntimeReplayInputs,
        settings: RuntimeReplaySettings | None = None,
        session_factory: Callable[[], Session] = get_session,
        close_session: bool = True,
        calendar: MarketCalendar | None = None,
        cycle_handlers: dict[str, CycleHandler] | None = None,
    ) -> None:
        self.inputs = inputs
        self.settings = settings or Settings()
        self.session_factory = session_factory
        self.close_session = close_session
        self.calendar = calendar or HistoricalMarketCalendar()
        self.cycle_handlers = cycle_handlers or {}

    def run(self) -> dict[str, Any]:
        cycles = expand_cycles(self.inputs.cycles)
        validate_cycles(cycles)
        self._assert_local_safe()

        replay_uuid = uuid.uuid4()
        replay_id = str(replay_uuid)
        first_tick = self.calendar.market_open(self.inputs.start_date)
        clock = HistoricalTradingClock(first_tick)
        session = self.session_factory()

        try:
            settings_snapshot = load_settings_snapshot(session=session, settings=self.settings)
            self._assert_control_state_safe(settings_snapshot)
            settings_snapshot_hash = snapshot_hash(settings_snapshot)

            if self.inputs.reset_sim_state:
                reset_simulated_runtime_state(session)

            state = ReplayExecutionState(
                cash=self.inputs.starting_cash,
                peak_equity=self.inputs.starting_cash,
                latest_equity=self.inputs.starting_cash,
            )
            counters = ReplayCounters()
            warnings: list[str] = []
            ctx = ReplayCycleContext(
                session=session,
                inputs=self.inputs,
                replay_id=replay_id,
                replay_uuid=replay_uuid,
                clock=clock,
                settings_snapshot=settings_snapshot,
                settings_snapshot_hash=settings_snapshot_hash,
                counters=counters,
                state=state,
                warnings=warnings,
            )

            _write_cash_snapshot(ctx)

            for tick in self.calendar.scheduled_times(
                self.inputs.start_date,
                self.inputs.end_date,
                timedelta(minutes=self.inputs.cadence_minutes),
                max_ticks=self.inputs.max_ticks,
            ):
                clock.advance_to(tick)
                state.tick_index += 1
                counters.trading_days_processed.add(tick.date())
                for cycle in cycles:
                    self._run_cycle(cycle, ctx)

            summary = build_summary(
                inputs=self.inputs,
                replay_id=replay_id,
                settings_snapshot=settings_snapshot,
                settings_snapshot_hash=settings_snapshot_hash,
                counters=counters,
                state=state,
                warnings=warnings,
                cycles=cycles,
            )
            if self.inputs.output_json is not None:
                write_json_summary(self.inputs.output_json, summary)
            session.commit()
            return summary
        finally:
            if self.close_session:
                session.close()

    def _assert_local_safe(self) -> None:
        if self.settings.trading_environment is TradingEnvironment.LIVE:
            raise RuntimeError("runtime replay-debug refuses to run with live trading environment")
        if not self.settings.no_live_trading:
            raise RuntimeError("runtime replay-debug requires NO_LIVE_TRADING=true")

    @staticmethod
    def _assert_control_state_safe(settings_snapshot: dict[str, Any]) -> None:
        control = settings_snapshot.get("runtime_control", {})
        if isinstance(control, dict) and str(control.get("trading_mode", "")).lower() == "live":
            raise RuntimeError(
                "runtime replay-debug refuses to run with persisted live trading mode"
            )

    def _run_cycle(self, cycle: str, ctx: ReplayCycleContext) -> None:
        handler = self.cycle_handlers.get(cycle) or DEFAULT_CYCLE_HANDLERS[cycle]
        repository = _SessionRuntimeJobRunRepository(ctx.session)
        runner = RuntimeJobRunner(repository=repository)
        input_summary: dict[str, object] = {
            "run_type": DEBUG_REPLAY_RUN_TYPE,
            "source": DEBUG_REPLAY_SOURCE,
            "replay_id": ctx.replay_id,
            "replay_timestamp": ctx.clock.now().isoformat(),
            "cycle": cycle,
            "symbols": ctx.inputs.symbols,
            "settings_snapshot_hash": ctx.settings_snapshot_hash,
        }

        try:
            output = runner.run(
                job_name=f"runtime_replay_debug.{cycle}",
                trigger_type="manual_cli",
                job=lambda: handler(ctx),
                correlation_id=ctx.replay_id,
                input_summary_json=input_summary,
            )
            ctx.counters.runtime_jobs_completed += 1
            if output is not None:
                _update_latest_runtime_job_output(ctx.session, ctx.replay_id, cycle, output)
        except Exception:
            ctx.counters.runtime_jobs_failed += 1
            raise
        finally:
            ctx.counters.cycle_invocations[cycle] += 1


def expand_cycles(cycles: list[str]) -> list[str]:
    expanded: list[str] = []
    for cycle in cycles:
        name = cycle.strip().lower()
        if not name:
            continue
        if name == "full_runtime_day":
            expanded.extend(FULL_RUNTIME_DAY)
        elif name == "market_ingestion":
            expanded.append("market_backfill")
        else:
            expanded.append(name)
    return expanded


def validate_cycles(cycles: list[str]) -> None:
    unknown = sorted(set(cycles) - SUPPORTED_CYCLES)
    if unknown:
        raise ValueError(f"Unsupported replay cycle(s): {', '.join(unknown)}")


def load_settings_snapshot(*, session: Session, settings: RuntimeReplaySettings) -> dict[str, Any]:
    operator = session.get(OperatorSettingsRow, "default")
    control = session.get(RuntimeControlState, "global")
    strategy_rows = list(
        session.scalars(select(StrategyControlState).order_by(StrategyControlState.strategy_id))
    )
    allocation_rows = AllocationOverridesRepository(session).get_all_active()
    operator_settings = _operator_settings_snapshot(operator)
    runtime_control = _runtime_control_snapshot(control)

    return {
        "loaded_at": datetime.now(UTC).isoformat(),
        "environment": {
            "app_env": settings.app_env,
            "trading_environment": settings.trading_environment.value,
            "no_live_trading": settings.no_live_trading,
            "shadow_mode_enabled": settings.shadow_mode_enabled,
            "max_gross_exposure": settings.max_gross_exposure,
            "max_net_exposure": settings.max_net_exposure,
            "max_symbol_exposure": settings.max_symbol_exposure,
            "max_leverage": settings.max_leverage,
            "max_orders_per_hour": settings.max_orders_per_hour,
            "max_orders_per_bar": settings.max_orders_per_bar,
            "max_daily_notional_traded": settings.max_daily_notional_traded,
            "max_reserved_cash": settings.max_reserved_cash,
        },
        "operator_settings": operator_settings,
        "runtime_control": runtime_control,
        "strategy_control_states": [
            {
                "strategy_id": row.strategy_id,
                "enabled": row.enabled,
                "reason": row.reason,
                "updated_by": row.updated_by,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in strategy_rows
        ],
        "allocation_overrides": [
            {
                "override_id": row.override_id,
                "strategy_id": row.strategy_id,
                "max_pct_of_capital": row.max_pct_of_capital,
                "max_position_size_usd": row.max_position_size_usd,
                "max_drawdown_allowed": row.max_drawdown_allowed,
                "is_active": row.is_active,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
            for row in allocation_rows
        ],
    }


def _operator_settings_snapshot(row: OperatorSettingsRow | None) -> dict[str, Any]:
    if row is None:
        return {
            "settings_id": "default",
            "source": "default_fallback_not_persisted",
            "risk_tolerance": "medium",
            "max_drawdown_limit": 0.10,
            "max_strategy_drawdown": 0.12,
            "rebalance_frequency": "weekly",
            "auto_promote_enabled": False,
            "min_sharpe_for_promotion": 1.5,
            "min_paper_trading_period_days": 30,
            "auto_demote_on_breach": True,
            "notify_drawdown_alerts": True,
            "notify_strategy_promotion_events": True,
            "notify_pipeline_failures": True,
            "per_strategy_cap": 0.25,
            "target_portfolio_volatility": 0.15,
            "slippage_model": "fixed",
            "transaction_cost_model": "per_share",
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "settings_id": row.settings_id,
        "source": "operator_settings",
        "risk_tolerance": row.risk_tolerance,
        "max_drawdown_limit": _float(row.max_drawdown_limit),
        "max_strategy_drawdown": _float(row.max_strategy_drawdown),
        "rebalance_frequency": row.rebalance_frequency,
        "auto_promote_enabled": row.auto_promote_enabled,
        "min_sharpe_for_promotion": _float(row.min_sharpe_for_promotion),
        "min_paper_trading_period_days": row.min_paper_trading_period_days,
        "auto_demote_on_breach": row.auto_demote_on_breach,
        "notify_drawdown_alerts": row.notify_drawdown_alerts,
        "notify_strategy_promotion_events": row.notify_strategy_promotion_events,
        "notify_pipeline_failures": row.notify_pipeline_failures,
        "per_strategy_cap": _float(row.per_strategy_cap),
        "target_portfolio_volatility": _float(row.target_portfolio_volatility),
        "slippage_model": row.slippage_model,
        "transaction_cost_model": row.transaction_cost_model,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat(),
    }


def _runtime_control_snapshot(row: RuntimeControlState | None) -> dict[str, Any]:
    if row is None:
        return {
            "control_id": "global",
            "source": "default_fallback_not_persisted",
            "trading_enabled": True,
            "trading_paused": False,
            "kill_switch_enabled": False,
            "trading_mode": "paper",
            "reason": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "control_id": row.control_id,
        "source": "runtime_control_state",
        "trading_enabled": row.trading_enabled,
        "trading_paused": row.trading_paused,
        "kill_switch_enabled": row.kill_switch_enabled,
        "trading_mode": row.trading_mode,
        "reason": row.reason,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat(),
    }


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    stable_snapshot = {k: v for k, v in snapshot.items() if k != "loaded_at"}
    payload = json.dumps(stable_snapshot, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def reset_simulated_runtime_state(session: Session) -> None:
    debug_run_ids = _debug_run_ids(session)

    for row in list(session.scalars(select(PositionSnapshotItem))):
        snapshot = session.get(PositionSnapshot, row.snapshot_id)
        if snapshot is not None and _is_simulation_source(snapshot.source):
            session.delete(row)

    for model in (CashSnapshot, PositionSnapshot):
        for row in list(session.scalars(select(model))):
            if _is_simulation_source(row.source):
                session.delete(row)

    for row in list(session.scalars(select(Fill))):
        if _is_debug_meta(row.meta) or str(row.run_id) in debug_run_ids:
            session.delete(row)

    for row in list(session.scalars(select(OrderIntents))):
        if _is_debug_meta(row.meta) or str(row.run_id) in debug_run_ids:
            session.delete(row)

    for row in list(session.scalars(select(BrokerOrder))):
        if _is_debug_meta(row.raw_broker_payload) or str(row.run_id) in debug_run_ids:
            session.delete(row)

    for row in list(session.scalars(select(Signal))):
        if _is_debug_meta(row.params) or str(row.run_id) in debug_run_ids:
            session.delete(row)

    for row in list(session.scalars(select(RiskSnapshot))):
        if _is_debug_meta(row.limits) or str(row.run_id) in debug_run_ids:
            session.delete(row)

    for row in list(session.scalars(select(AuditLogRow))):
        if row.component.startswith("runtime_replay_debug") or _is_debug_meta(row.event_metadata):
            session.delete(row)

    for row in list(session.scalars(select(RuntimeJobRuns))):
        if _is_debug_meta(row.input_summary_json) or _is_debug_meta(row.output_summary_json):
            session.delete(row)

    session.flush()


def _debug_run_ids(session: Session) -> set[str]:
    ids: set[str] = set()
    for row in list(session.scalars(select(RuntimeJobRuns))):
        payload = row.input_summary_json or {}
        if _is_debug_meta(payload):
            replay_id = payload.get("replay_id")
            if isinstance(replay_id, str):
                ids.add(replay_id)
    return ids


def _is_debug_meta(value: dict[str, Any] | None) -> bool:
    if not value:
        return False
    return (
        value.get("source") == DEBUG_REPLAY_SOURCE
        or value.get("run_type") == DEBUG_REPLAY_RUN_TYPE
        or value.get("debug_replay") is True
    )


def _is_simulation_source(source: object) -> bool:
    return source in {OrderSource.SIMULATION, OrderSource.BACKTEST, "simulation", "backtest"}


def _run_market_data_check(ctx: ReplayCycleContext) -> dict[str, Any]:
    now = ctx.clock.now()
    bars = _load_tick_prices(ctx.session, ctx.inputs.symbols, now, ctx.inputs.price_basis)
    ctx.state.current_prices.update(bars)
    ctx.counters.market_data_points_seen += len(bars)
    if not bars:
        ctx.warnings.append(
            f"No persisted market bars found at {now.isoformat()}; synthetic prices used"
        )
    return {"bars_seen": len(bars)}


def _run_features_check(ctx: ReplayCycleContext) -> dict[str, Any]:
    return {"status": "checked", "note": "feature validation placeholder for local replay"}


def _run_runtime_checks(ctx: ReplayCycleContext) -> dict[str, Any]:
    return {"status": "checked"}


def _run_trading(ctx: ReplayCycleContext) -> dict[str, Any]:
    control = ctx.settings_snapshot["runtime_control"]
    if control["kill_switch_enabled"]:
        ctx.counters.orders_blocked_by_risk += len(ctx.inputs.symbols)
        _write_risk_snapshot(ctx, is_blocked=True, reasons=["kill_switch_enabled"])
        return {"status": "skipped", "skip_reason": "kill_switch_enabled"}
    if control["trading_paused"] or not control["trading_enabled"]:
        reason = "trading_paused" if control["trading_paused"] else "trading_disabled"
        ctx.counters.orders_blocked_by_risk += len(ctx.inputs.symbols)
        _write_risk_snapshot(ctx, is_blocked=True, reasons=[reason])
        return {"status": "skipped", "skip_reason": reason}

    strategy_ids = _active_strategy_ids(ctx.settings_snapshot)

    prices = _prices_for_tick(ctx)
    equity = _mark_to_market(ctx.state, prices)
    ctx.state.latest_equity = equity
    ctx.state.peak_equity = max(ctx.state.peak_equity, equity)
    drawdown = _drawdown(ctx.state)
    max_drawdown_limit = Decimal(
        str(ctx.settings_snapshot["operator_settings"]["max_drawdown_limit"])
    )
    risk_blocked = abs(drawdown) >= max_drawdown_limit
    block_reasons: list[str] = []
    if risk_blocked:
        block_reasons.append("max_drawdown_limit")

    fills_created = 0
    orders_submitted = 0
    signals_generated = 0
    orders_attempted = 0
    orders_blocked = 0

    for idx, symbol in enumerate(ctx.inputs.symbols):
        strategy_id = strategy_ids[idx % len(strategy_ids)]
        price = prices[symbol]
        side = _decision_for(ctx, symbol)
        signal_id = uuid.uuid5(
            ctx.replay_uuid,
            f"signal:{ctx.clock.now().isoformat()}:{symbol}:{ctx.state.tick_index}",
        )
        ctx.session.add(
            Signal(
                signal_id=signal_id,
                run_id=ctx.replay_uuid,
                timestamp=ctx.clock.now(),
                bar_timestamp=ctx.clock.now(),
                strategy_id=strategy_id,
                symbol=symbol,
                direction=SignalDirection.BUY if side == Side.BUY else SignalDirection.SELL,
                confidence=0.75,
                target_position=None,
                params=_debug_metadata(ctx),
            )
        )
        signals_generated += 1
        orders_attempted += 1

        if risk_blocked:
            orders_blocked += 1
            continue

        qty = _quantity_for_order(ctx, symbol, side, price, strategy_id)
        if qty <= 0:
            continue

        _apply_fill(ctx.state, symbol, side, qty, price)
        _write_order_and_fill(
            ctx, symbol=symbol, side=side, qty=qty, price=price, strategy_id=strategy_id
        )
        orders_submitted += 1
        fills_created += 1

    ctx.state.latest_equity = _mark_to_market(ctx.state, prices)
    ctx.state.peak_equity = max(ctx.state.peak_equity, ctx.state.latest_equity)
    _write_risk_snapshot(ctx, is_blocked=risk_blocked, reasons=block_reasons)

    ctx.counters.signals_generated += signals_generated
    ctx.counters.orders_attempted += orders_attempted
    ctx.counters.orders_submitted += orders_submitted
    ctx.counters.orders_blocked_by_risk += orders_blocked
    ctx.counters.fills_created += fills_created
    return {
        "signals_generated": signals_generated,
        "orders_attempted": orders_attempted,
        "orders_submitted": orders_submitted,
        "orders_blocked_by_risk": orders_blocked,
        "fills_created": fills_created,
        "drawdown": float(drawdown),
    }


def _run_rebalance(ctx: ReplayCycleContext) -> dict[str, Any]:
    ctx.counters.rebalance_checks += 1
    frequency = str(ctx.settings_snapshot["operator_settings"]["rebalance_frequency"]).lower()
    today = ctx.clock.now().date()
    triggered = False
    reason = "frequency_not_due"
    if ctx.state.last_rebalance_date is None:
        triggered = True
        reason = "initial_rebalance"
    elif frequency == "daily":
        triggered = today > ctx.state.last_rebalance_date
    elif frequency == "weekly":
        triggered = today.isocalendar().week != ctx.state.last_rebalance_date.isocalendar().week
    elif frequency == "monthly":
        triggered = (today.year, today.month) != (
            ctx.state.last_rebalance_date.year,
            ctx.state.last_rebalance_date.month,
        )
    else:
        reason = f"unsupported_frequency:{frequency}"

    if triggered:
        ctx.state.last_rebalance_date = today
        ctx.counters.rebalances_triggered += 1
        return {"triggered": True, "reason": reason, "rebalance_frequency": frequency}

    ctx.counters.rebalances_skipped += 1
    ctx.counters.rebalance_skip_reasons[reason] += 1
    return {"triggered": False, "reason": reason, "rebalance_frequency": frequency}


def _run_portfolio_snapshot(ctx: ReplayCycleContext) -> dict[str, Any]:
    _write_position_snapshot(ctx)
    _write_cash_snapshot(ctx)
    ctx.counters.portfolio_snapshots += 1
    return {
        "cash": float(ctx.state.cash),
        "equity": float(ctx.state.latest_equity),
        "positions": len(ctx.state.positions),
    }


def _run_noop_eod(ctx: ReplayCycleContext) -> dict[str, Any]:
    return {"status": "checked"}


DEFAULT_CYCLE_HANDLERS: dict[str, CycleHandler] = {
    "market_backfill": _run_market_data_check,
    "market_ingestion": _run_market_data_check,
    "features": _run_features_check,
    "trading": _run_trading,
    "rebalance": _run_rebalance,
    "portfolio_snapshot": _run_portfolio_snapshot,
    "corporate_actions": _run_noop_eod,
    "eod": _run_noop_eod,
    "runtime_checks": _run_runtime_checks,
}


def _prices_for_tick(ctx: ReplayCycleContext) -> dict[str, Decimal]:
    prices = _load_tick_prices(
        ctx.session, ctx.inputs.symbols, ctx.clock.now(), ctx.inputs.price_basis
    )
    resolved: dict[str, Decimal] = {}
    for idx, symbol in enumerate(ctx.inputs.symbols):
        resolved[symbol] = prices.get(symbol) or _synthetic_price(ctx, symbol, idx)
    ctx.state.current_prices.update(resolved)
    return resolved


def _load_tick_prices(
    session: Session,
    symbols: list[str],
    timestamp: datetime,
    price_basis: PriceBasis,
) -> dict[str, Decimal]:
    if not symbols:
        return {}
    rows = list(
        session.scalars(
            select(MarketBar)
            .where(MarketBar.symbol.in_(symbols))
            .where(MarketBar.timestamp <= timestamp)
            .where(MarketBar.price_basis == price_basis)
            .order_by(MarketBar.timestamp.desc())
        )
    )
    prices: dict[str, Decimal] = {}
    for row in rows:
        if row.symbol not in prices:
            prices[row.symbol] = Decimal(str(row.close))
    return prices


def _synthetic_price(ctx: ReplayCycleContext, symbol: str, index: int) -> Decimal:
    base = Decimal("100") + Decimal(index * 5)
    trend = Decimal(ctx.state.tick_index - 1) * Decimal("-2.25")
    jitter_seed = f"{ctx.inputs.random_seed}:{symbol}:{ctx.clock.now().date().isoformat()}"
    jitter = Decimal(str(random.Random(jitter_seed).uniform(-0.35, 0.35))).quantize(
        Decimal("0.0001")
    )
    return max(Decimal("1"), base + trend + jitter)


def _decision_for(ctx: ReplayCycleContext, symbol: str) -> Side:
    if ctx.state.positions.get(symbol, Decimal("0")) <= 0:
        return Side.BUY
    if ctx.state.tick_index % 7 == 0:
        return Side.SELL
    return Side.BUY


def _quantity_for_order(
    ctx: ReplayCycleContext,
    symbol: str,
    side: Side,
    price: Decimal,
    strategy_id: str,
) -> Decimal:
    if side == Side.SELL:
        return ctx.state.positions.get(symbol, Decimal("0"))

    equity = max(ctx.state.latest_equity, ctx.inputs.starting_cash)
    cap = Decimal(str(ctx.settings_snapshot["operator_settings"]["per_strategy_cap"]))
    override = _allocation_override(ctx.settings_snapshot, strategy_id)
    if override and override.get("max_pct_of_capital") is not None:
        cap = min(cap, Decimal(str(override["max_pct_of_capital"])))

    notional = (equity * cap) / Decimal(max(1, len(ctx.inputs.symbols)))
    if override and override.get("max_position_size_usd") is not None:
        notional = min(notional, Decimal(str(override["max_position_size_usd"])))
    notional = min(notional, ctx.state.cash)
    return (notional / price).quantize(Decimal("0.0001"))


def _apply_fill(
    state: ReplayExecutionState,
    symbol: str,
    side: Side,
    qty: Decimal,
    price: Decimal,
) -> None:
    if side == Side.BUY:
        total = qty * price
        previous_qty = state.positions.get(symbol, Decimal("0"))
        previous_cost = state.avg_costs.get(symbol, Decimal("0")) * previous_qty
        new_qty = previous_qty + qty
        state.positions[symbol] = new_qty
        state.avg_costs[symbol] = (previous_cost + total) / new_qty
        state.cash -= total
        return

    held = state.positions.get(symbol, Decimal("0"))
    sell_qty = min(qty, held)
    state.cash += sell_qty * price
    remaining = held - sell_qty
    if remaining <= 0:
        state.positions.pop(symbol, None)
        state.avg_costs.pop(symbol, None)
    else:
        state.positions[symbol] = remaining


def _write_order_and_fill(
    ctx: ReplayCycleContext,
    *,
    symbol: str,
    side: Side,
    qty: Decimal,
    price: Decimal,
    strategy_id: str,
) -> None:
    now = ctx.clock.now()
    intent_id = uuid.uuid5(ctx.replay_uuid, f"intent:{now.isoformat()}:{symbol}:{side.value}")
    broker_order_id = str(
        uuid.uuid5(ctx.replay_uuid, f"broker:{now.isoformat()}:{symbol}:{side.value}")
    )
    client_order_id = f"rdbg-{broker_order_id[:20]}"
    ctx.session.add(
        OrderIntents(
            intent_id=intent_id,
            idempotency_key=f"{ctx.replay_id}:{now.isoformat()}:{symbol}:{side.value}",
            run_id=ctx.replay_uuid,
            strategy_id=strategy_id,
            timestamp=now,
            bar_timestamp=now,
            symbol=symbol,
            side=side,
            qty=qty,
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=client_order_id,
            meta=_debug_metadata(ctx),
        )
    )
    ctx.session.add(
        BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            intent_id=intent_id,
            run_id=ctx.replay_uuid,
            broker="local_debug_replay",
            account_id="local_simulation",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            qty=qty,
            notional=None,
            limit_price=None,
            stop_price=None,
            status=OrderStatus.FILLED,
            submitted_at=now,
            updated_at=now,
            filled_qty=qty,
            avg_fill_price=price,
            last_error=None,
            raw_broker_payload=_debug_metadata(ctx),
            requested_qty=qty,
        )
    )
    fill_id = f"rdbg_{ctx.replay_id[:8]}_{symbol}_{side.value}_{ctx.state.tick_index}"
    ctx.session.add(
        Fill(
            fill_id=fill_id[:64],
            broker_order_id=broker_order_id,
            intent_id=intent_id,
            run_id=ctx.replay_uuid,
            timestamp=now,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            fees=Decimal("0"),
            liquidity=LiquiditySide.TAKER,
            venue="local_debug_replay",
            meta=_debug_metadata(ctx),
        )
    )


def _write_position_snapshot(ctx: ReplayCycleContext) -> None:
    snapshot = PositionSnapshot(
        snapshot_id=uuid.uuid4(),
        run_id=ctx.replay_uuid,
        timestamp=ctx.clock.now(),
        source=OrderSource.SIMULATION,
    )
    ctx.session.add(snapshot)
    ctx.session.flush()
    for symbol, qty in ctx.state.positions.items():
        price = ctx.state.current_prices.get(symbol, Decimal("0"))
        avg_cost = ctx.state.avg_costs.get(symbol)
        ctx.session.add(
            PositionSnapshotItem(
                snapshot_id=snapshot.snapshot_id,
                symbol=symbol,
                quantity=qty,
                avg_cost=avg_cost,
                market_price=price,
                market_value=price * qty,
                unrealized_pnl=(price - avg_cost) * qty if avg_cost is not None else None,
            )
        )


def _write_cash_snapshot(ctx: ReplayCycleContext) -> None:
    equity = _mark_to_market(ctx.state, ctx.state.current_prices)
    ctx.state.latest_equity = equity
    ctx.state.peak_equity = max(ctx.state.peak_equity, equity)
    ctx.session.add(
        CashSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=ctx.replay_uuid,
            timestamp=ctx.clock.now(),
            currency="USD",
            cash=ctx.state.cash,
            buying_power=ctx.state.cash,
            reserved_cash=Decimal("0"),
            equity=equity,
            source=OrderSource.SIMULATION,
            capital_bucket=ctx.inputs.starting_cash,
        )
    )


def _write_risk_snapshot(
    ctx: ReplayCycleContext,
    *,
    is_blocked: bool,
    reasons: list[str],
) -> None:
    equity = _mark_to_market(ctx.state, ctx.state.current_prices)
    gross_exposure = sum(
        (
            qty * ctx.state.current_prices.get(symbol, Decimal("0"))
            for symbol, qty in ctx.state.positions.items()
        ),
        Decimal("0"),
    )
    ctx.session.add(
        RiskSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=ctx.replay_uuid,
            timestamp=ctx.clock.now(),
            gross_exposure=gross_exposure,
            net_exposure=gross_exposure,
            leverage=float(gross_exposure / equity) if equity else 0.0,
            drawdown_pct=float(_drawdown(ctx.state)),
            limits={
                **_debug_metadata(ctx),
                "max_drawdown_limit": ctx.settings_snapshot["operator_settings"][
                    "max_drawdown_limit"
                ],
                "block_reasons": reasons,
            },
            utilization={"equity": float(equity), "gross_exposure": float(gross_exposure)},
            is_blocked=is_blocked,
            block_reasons=None,
        )
    )


def _mark_to_market(state: ReplayExecutionState, prices: dict[str, Decimal]) -> Decimal:
    equity = state.cash
    for symbol, qty in state.positions.items():
        equity += qty * prices.get(symbol, state.current_prices.get(symbol, Decimal("0")))
    return equity


def _drawdown(state: ReplayExecutionState) -> Decimal:
    if state.peak_equity <= 0:
        return Decimal("0")
    return (state.latest_equity - state.peak_equity) / state.peak_equity


def _active_strategy_ids(snapshot: dict[str, Any]) -> list[str]:
    """Return enabled strategy IDs from seeded controls, falling back to baseline."""
    enabled = [row["strategy_id"] for row in snapshot["strategy_control_states"] if row["enabled"]]
    return enabled if enabled else [DEFAULT_STRATEGY_ID]


def _allocation_override(snapshot: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    for row in snapshot["allocation_overrides"]:
        if row["strategy_id"] == strategy_id and row["is_active"]:
            return cast(dict[str, Any], row)
    return None


def _debug_metadata(ctx: ReplayCycleContext) -> dict[str, Any]:
    return {
        "source": DEBUG_REPLAY_SOURCE,
        "run_type": DEBUG_REPLAY_RUN_TYPE,
        "replay_id": ctx.replay_id,
        "settings_snapshot_hash": ctx.settings_snapshot_hash,
    }


def _update_latest_runtime_job_output(
    session: Session,
    replay_id: str,
    cycle: str,
    output: dict[str, Any],
) -> None:
    row = (
        session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == replay_id)
        .filter(RuntimeJobRuns.job_name == f"runtime_replay_debug.{cycle}")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )
    if isinstance(row, RuntimeJobRuns):
        row.output_summary_json = {
            **(row.output_summary_json or {}),
            **output,
            "source": DEBUG_REPLAY_SOURCE,
            "run_type": DEBUG_REPLAY_RUN_TYPE,
        }
        session.flush()


def build_summary(
    *,
    inputs: RuntimeReplayInputs,
    replay_id: str,
    settings_snapshot: dict[str, Any],
    settings_snapshot_hash: str,
    counters: ReplayCounters,
    state: ReplayExecutionState,
    warnings: list[str],
    cycles: list[str],
) -> dict[str, Any]:
    ending_equity = state.latest_equity
    total_return_pct = (
        (ending_equity - inputs.starting_cash) / inputs.starting_cash * Decimal("100")
        if inputs.starting_cash
        else Decimal("0")
    )
    return {
        "replay_id": replay_id,
        "inputs": {
            "date_range": {
                "start": inputs.start_date.isoformat(),
                "end": inputs.end_date.isoformat(),
            },
            "symbols": inputs.symbols,
            "starting_cash": float(inputs.starting_cash),
            "random_seed": inputs.random_seed,
            "price_basis": inputs.price_basis.value,
            "calendar_mode": inputs.calendar_mode,
            "cycles": cycles,
            "cadence_minutes": inputs.cadence_minutes,
            "max_ticks": inputs.max_ticks,
        },
        "loaded_settings": settings_snapshot,
        "settings_snapshot_hash": settings_snapshot_hash,
        "execution": {
            "trading_days_processed": len(counters.trading_days_processed),
            "cycle_invocations": dict(counters.cycle_invocations),
            "runtime_jobs_completed": counters.runtime_jobs_completed,
            "runtime_jobs_failed": counters.runtime_jobs_failed,
        },
        "trading": {
            "signals_generated": counters.signals_generated,
            "orders_attempted": counters.orders_attempted,
            "orders_submitted": counters.orders_submitted,
            "orders_blocked_by_risk": counters.orders_blocked_by_risk,
            "fills_created": counters.fills_created,
        },
        "rebalancing": {
            "rebalance_checks": counters.rebalance_checks,
            "rebalances_triggered": counters.rebalances_triggered,
            "rebalances_skipped": counters.rebalances_skipped,
            "skip_reasons": dict(counters.rebalance_skip_reasons),
        },
        "portfolio": {
            "starting_equity": float(inputs.starting_cash),
            "ending_equity": float(ending_equity),
            "cash": float(state.cash),
            "positions": {symbol: float(qty) for symbol, qty in state.positions.items()},
            "total_return_pct": float(total_return_pct),
            "portfolio_snapshots": counters.portfolio_snapshots,
        },
        "risk_metrics": {
            "max_drawdown_observed": float(_drawdown(state)),
            "peak_equity": float(state.peak_equity),
        },
        "market_data": {
            "market_data_points_seen": counters.market_data_points_seen,
        },
        "warnings": warnings,
    }


def write_json_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def format_text_summary(summary: dict[str, Any]) -> str:
    inputs = summary["inputs"]
    settings = summary["loaded_settings"]
    operator = settings["operator_settings"]
    control = settings["runtime_control"]
    trading = summary["trading"]
    rebalancing = summary["rebalancing"]
    portfolio = summary["portfolio"]
    risk = summary["risk_metrics"]
    execution = summary["execution"]
    lines = [
        "=== Runtime Replay Debug Summary ===",
        "",
        "Inputs:",
        f"- Date range: {inputs['date_range']['start']} -> {inputs['date_range']['end']}",
        f"- Symbols: {', '.join(inputs['symbols'])}",
        f"- Starting cash: {inputs['starting_cash']}",
        f"- Random seed: {inputs['random_seed']}",
        f"- Price basis: {inputs['price_basis']}",
        f"- Cycles: {', '.join(inputs['cycles'])}",
        "",
        "Loaded settings:",
        f"- trading_mode: {control['trading_mode']}",
        f"- trading_enabled: {control['trading_enabled']}",
        f"- trading_paused: {control['trading_paused']}",
        f"- kill_switch_enabled: {control['kill_switch_enabled']}",
        f"- max_drawdown_limit: {operator['max_drawdown_limit']}",
        f"- max_strategy_drawdown: {operator['max_strategy_drawdown']}",
        f"- rebalance_frequency: {operator['rebalance_frequency']}",
        f"- per_strategy_cap: {operator['per_strategy_cap']}",
        f"- enabled_strategies: {_enabled_strategy_labels(settings)}",
        f"- allocation_overrides: {settings['allocation_overrides']}",
        "",
        "Execution:",
        f"- trading_days_processed: {execution['trading_days_processed']}",
        f"- cycle_invocations: {execution['cycle_invocations']}",
        f"- runtime_jobs_completed: {execution['runtime_jobs_completed']}",
        f"- runtime_jobs_failed: {execution['runtime_jobs_failed']}",
        "",
        "Trading:",
        f"- signals_generated: {trading['signals_generated']}",
        f"- orders_attempted: {trading['orders_attempted']}",
        f"- orders_submitted: {trading['orders_submitted']}",
        f"- orders_blocked_by_risk: {trading['orders_blocked_by_risk']}",
        f"- fills_created: {trading['fills_created']}",
        "",
        "Rebalancing:",
        f"- rebalance_checks: {rebalancing['rebalance_checks']}",
        f"- rebalances_triggered: {rebalancing['rebalances_triggered']}",
        f"- rebalances_skipped: {rebalancing['rebalances_skipped']}",
        f"- skip_reasons: {rebalancing['skip_reasons']}",
        "",
        "Portfolio:",
        f"- starting_equity: {portfolio['starting_equity']}",
        f"- ending_equity: {portfolio['ending_equity']}",
        f"- total_return_pct: {portfolio['total_return_pct']:.4f}",
        f"- positions: {portfolio['positions']}",
        "",
        "Diagnostics:",
        f"- max_drawdown_observed: {risk['max_drawdown_observed']:.6f}",
        f"- settings_snapshot_hash: {summary['settings_snapshot_hash']}",
        f"- warnings: {summary['warnings']}",
    ]
    return "\n".join(lines)


def _enabled_strategy_labels(settings: dict[str, Any]) -> list[str]:
    rows = settings["strategy_control_states"]
    if not rows:
        return ["baseline_strategy(default_enabled)"]
    return [row["strategy_id"] for row in rows if row["enabled"]]


def _float(value: object) -> float:
    return float(Decimal(str(value)))
