# autonomous_trading_platform/scheduler/jobs/portfolio_evaluation.py
"""
Portfolio-mode trading evaluation: every active strategy, each against its own sleeve.

Flow per cycle:
  1. Adopt account shares no sleeve owns into the unattributed sleeve (only when no
     orders are open, so an in-flight fill is never mistaken for an orphan).
  2. Evaluate each ACTIVE strategy; one strategy failing does not stop the others.
  3. Size each strategy against its own budget and diff targets against its own
     sleeve, then trim buys so the whole sleeve stays within the budget (the sizer
     sizes each position from the full allocation). WINDING_DOWN strategies and
     orphan sleeves only exit.
  4. Cross opposing orders between sleeves internally; send only residuals.
  5. Never let sells for a symbol exceed what the account actually holds.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import (
    SleeveReconciliationReport,
)
from autonomous_trading_platform.contracts.common.enums import Side, StrategyEvent
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.trading.signal_aggregate import SignalNettingPolicy
from autonomous_trading_platform.execution.services.portfolio_signal_aggregator import (
    PortfolioSignalAggregator,
)
from autonomous_trading_platform.execution.services.sleeve_crossing_service import (
    PlannedCross,
    SleeveCrossingService,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    SleeveAccountingError,
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    StrategyRuntime,
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import EvaluateStrategyJob

logger = get_logger(__name__)


@dataclass
class StrategyEvaluationOutcome:
    strategy_id: str
    # evaluated | bars_not_ready | failed | disabled | winding_down
    status: str
    signal_count: int = 0
    intent_count: int = 0
    error: str | None = None


@dataclass
class PortfolioEvaluationResult:
    outcomes: list[StrategyEvaluationOutcome]
    crosses: list[PlannedCross] = field(default_factory=list)
    adoption: SleeveReconciliationReport | None = None
    clamped_sells: dict[str, Decimal] = field(default_factory=dict)

    @property
    def evaluated(self) -> bool:
        return any(o.status == "evaluated" for o in self.outcomes)


def run_portfolio_evaluation(
    *,
    now_utc: datetime,
    deps: TradingCycleDependencies,
    manifest: RunManifest,
    fetch_positions: Any,
    fetch_prices: Any,
    fetch_recent_closes: Any,
    vol_lookback_bars: int,
    job_span: Any,
) -> tuple[PortfolioEvaluationResult, list[OrderIntent]]:
    session = deps.session
    execution_context = deps.execution_context
    broker_client = execution_context.broker_client
    construction = execution_context.portfolio_construction_service
    ledger = StrategySleeveLedgerService()

    account_positions: dict[str, Position] = fetch_positions(broker_client)

    # 1. Adopt unowned account shares, then load every sleeve.
    adoption: SleeveReconciliationReport | None = None
    with SorUnitOfWork(session) as uow:
        open_orders = execution_context.order_runtime_state_service.list_reconciliation_inputs(
            uow=uow
        )
        if not open_orders:
            adoption = ledger.reconcile(
                uow,
                account_positions=account_positions,
                timestamp=now_utc,
                adopt_unowned=True,
            )
            if adoption.adopted_symbols:
                logger.warning(
                    "portfolio_evaluation.unowned_positions_adopted",
                    extra={"symbols": adoption.adopted_symbols, "run_id": str(manifest.run_id)},
                )
        sleeves = ledger.all_positions(uow)
        runtimes = list(deps.strategy_runtimes or [])
        known = {runtime.strategy_id for runtime in runtimes}
        for orphan in sorted(set(sleeves) - known):
            runtimes.append(
                StrategyRuntime(
                    strategy_id=orphan,
                    status=MembershipStatus.WINDING_DOWN,
                    governance_state=GovernanceState.APPROVED_PAPER,
                    budget_pct=Decimal("0"),
                )
            )
        enabled = {
            runtime.strategy_id: uow.strategy_control_states.is_enabled(runtime.strategy_id)
            for runtime in runtimes
        }

    # 2. Evaluate ACTIVE strategies.
    outcomes: list[StrategyEvaluationOutcome] = []
    signals_by_strategy: dict[str, list[Signal]] = {}
    bar_by_strategy: dict[str, datetime] = {}
    first_error: Exception | None = None
    active_enabled = 0
    for runtime in runtimes:
        sid = runtime.strategy_id
        if not enabled[sid]:
            # Operator-disabled strategies are frozen: no entries, no exits.
            outcomes.append(StrategyEvaluationOutcome(sid, "disabled"))
            continue
        if runtime.status != MembershipStatus.ACTIVE or runtime.strategy_context is None:
            continue
        active_enabled += 1
        context = runtime.strategy_context
        try:
            job_result = EvaluateStrategyJob(
                readiness_service=context.strategy_bar_readiness_service,
                evaluation_service=context.strategy_evaluation_service,
                signal_writer=context.signal_writer,
                checkpoint_writer=context.strategy_checkpoint_writer,
                run_manifest_service=deps.manifest_service,
            ).run(now=now_utc, parent_run_id=str(manifest.run_id))
        except Exception as exc:
            logger.exception(
                "portfolio_evaluation.strategy_evaluation_failed",
                extra={"strategy_id": sid, "run_id": str(manifest.run_id)},
            )
            outcomes.append(StrategyEvaluationOutcome(sid, "failed", error=str(exc)))
            first_error = first_error or exc
            continue
        if not job_result.evaluated:
            outcomes.append(StrategyEvaluationOutcome(sid, "bars_not_ready"))
            continue
        signals_by_strategy[sid] = list(job_result.signals)
        if job_result.target_bar_timestamp is not None:
            bar_by_strategy[sid] = job_result.target_bar_timestamp
        if job_result.signals:
            _apply_state_event(deps, sid, StrategyEvent.SIGNAL_GENERATED, now_utc)

    failed = sum(1 for o in outcomes if o.status == "failed")
    if first_error is not None and active_enabled and failed == active_enabled:
        # Every active strategy failed: surface it so the cycle's degraded-mode
        # handling (hold positions) behaves as it does for a single strategy.
        raise first_error

    fallback_bar = max(bar_by_strategy.values(), default=None) or manifest.bar_timestamp or now_utc

    # 3. Prices for everything that could trade, then per-strategy intents.
    symbols: set[str] = set()
    for signals in signals_by_strategy.values():
        symbols.update(s.symbol for s in signals)
    for runtime in runtimes:
        if enabled[runtime.strategy_id]:
            symbols.update(sleeves.get(runtime.strategy_id, {}))
    prices: dict[str, float] = fetch_prices(broker_client, sorted(symbols))

    if len(signals_by_strategy) > 1:
        aggregation = PortfolioSignalAggregator(policy=SignalNettingPolicy.CONSERVATIVE).aggregate(
            signals_by_strategy=signals_by_strategy,
            run_id=manifest.run_id,
            bar_timestamp=fallback_bar,
            prices=prices,
        )
        # Telemetry only: each strategy trades its own sleeve, so conflicts are
        # resolved by internal crossing rather than by suppressing signals.
        job_span.set_attribute("ratp.aggregation_conflicts", aggregation.total_conflicts_detected)

    intents: list[OrderIntent] = []
    evaluated_ids = set(signals_by_strategy)
    for runtime in runtimes:
        sid = runtime.strategy_id
        if not enabled[sid]:
            continue
        is_active = runtime.status == MembershipStatus.ACTIVE
        if is_active and sid not in evaluated_ids:
            continue  # failed or bars not ready: hold this sleeve as-is
        signals = signals_by_strategy.get(sid, []) if is_active else []
        positions = {
            symbol: Position(symbol=symbol, quantity=held.quantity, avg_cost=held.avg_cost)
            for symbol, held in sleeves.get(sid, {}).items()
        }
        if not signals and not positions:
            if is_active:
                outcomes.append(StrategyEvaluationOutcome(sid, "evaluated"))
            continue

        bar_timestamp = bar_by_strategy.get(sid, fallback_bar)
        recent_closes = (
            fetch_recent_closes(
                strategy_context=runtime.strategy_context,
                symbols=sorted({s.symbol for s in signals}),
                bar_timestamp=bar_timestamp,
                lookback_bars=vol_lookback_bars,
            )
            if signals and runtime.strategy_context is not None
            else {}
        )
        generated = list(
            construction.generate_order_intents(
                signals=signals,
                positions=positions,
                prices=prices,
                run_id=manifest.run_id,
                strategy_id=sid,
                approval_status=runtime.governance_state,
                bar_timestamp=bar_timestamp,
                now=now_utc,
                recent_closes=recent_closes,
                skip_order_limit_breaches=True,
            )
        )
        budget_usd = Decimal(str(runtime.budget_pct)) * Decimal(
            str(deps.portfolio_engine.total_capital)
        )
        generated, trimmed_usd = _cap_buys_to_budget(
            generated,
            positions,
            budget_usd=budget_usd,
            construction=construction,
            prices=prices,
        )
        if trimmed_usd > 0:
            logger.info(
                "portfolio_evaluation.buys_trimmed_to_sleeve_budget",
                extra={
                    "strategy_id": sid,
                    "budget_usd": str(budget_usd),
                    "trimmed_usd": str(trimmed_usd),
                },
            )
        intents.extend(generated)
        outcomes.append(
            StrategyEvaluationOutcome(
                sid,
                "evaluated" if is_active else "winding_down",
                signal_count=len(signals),
                intent_count=len(generated),
            )
        )
        if is_active and signals and not generated:
            _apply_state_event(deps, sid, StrategyEvent.RESET, now_utc)

    # 4. Internal crossing between sleeves.
    plan = SleeveCrossingService(construction).plan(intents, prices=prices, run_id=manifest.run_id)
    crosses: list[PlannedCross] = []
    if plan.crosses:
        try:
            with SorUnitOfWork(session) as uow:
                for cross in plan.crosses:
                    ledger.apply_internal_cross(
                        uow,
                        cross_id=cross.cross_id,
                        symbol=cross.symbol,
                        quantity=cross.quantity,
                        price=cross.price,
                        buyer_strategy_id=cross.buyer_strategy_id,
                        seller_strategy_id=cross.seller_strategy_id,
                        timestamp=now_utc,
                        run_id=manifest.run_id,
                    )
            intents, crosses = plan.residual_intents, plan.crosses
        except SleeveAccountingError:
            logger.exception(
                "portfolio_evaluation.internal_cross_failed_sending_uncrossed",
                extra={"run_id": str(manifest.run_id)},
            )

    # 5. Sells may never exceed what the account holds (sleeve drift must not short).
    intents, clamped = _clamp_sells_to_account(
        intents, account_positions, construction=construction, prices=prices
    )
    if clamped:
        logger.error(
            "portfolio_evaluation.sells_clamped_to_account_holdings",
            extra={"clamped": {k: str(v) for k, v in clamped.items()}},
        )

    job_span.set_attribute("ratp.portfolio.strategy_count", len(runtimes))
    job_span.set_attribute("ratp.portfolio.internal_crosses", len(crosses))
    job_span.set_attribute("ratp.portfolio.intent_count", len(intents))
    logger.info(
        "portfolio_evaluation.completed",
        extra={
            "run_id": str(manifest.run_id),
            "outcomes": {o.strategy_id: o.status for o in outcomes},
            "intents": len(intents),
            "crosses": len(crosses),
        },
    )
    return (
        PortfolioEvaluationResult(
            outcomes=outcomes, crosses=crosses, adoption=adoption, clamped_sells=clamped
        ),
        intents,
    )


def _cap_buys_to_budget(
    intents: list[OrderIntent],
    positions: dict[str, Position],
    *,
    budget_usd: Decimal,
    construction: Any,
    prices: dict[str, float],
) -> tuple[list[OrderIntent], Decimal]:
    """Scale one strategy's buys so its projected sleeve value fits its budget.

    Projected value = held value - sells + buys, at current prices (avg cost when a
    held symbol has no price). Sells are never touched, so exits always go through.
    Returns (intents, buy notional removed).
    """

    def price_of(symbol: str) -> Decimal:
        if symbol in prices:
            return Decimal(str(prices[symbol]))
        held = positions.get(symbol)
        return Decimal(held.avg_cost) if held is not None and held.avg_cost else Decimal("0")

    held_value = sum(
        (Decimal(p.quantity) * price_of(sym) for sym, p in positions.items()), Decimal("0")
    )
    sells_value = sum(
        (Decimal(i.qty or 0) * price_of(i.symbol) for i in intents if i.side == Side.SELL),
        Decimal("0"),
    )
    buys = [i for i in intents if i.side == Side.BUY]
    buys_value = sum((Decimal(i.qty or 0) * price_of(i.symbol) for i in buys), Decimal("0"))
    room = max(budget_usd - (held_value - sells_value), Decimal("0"))
    if buys_value <= room or buys_value == 0:
        return intents, Decimal("0")

    scale = room / buys_value
    replaced: dict[Any, OrderIntent | None] = {}
    kept_value = Decimal("0")
    for intent in buys:
        qty = int(Decimal(intent.qty or 0) * scale)  # floor: never exceed the budget
        if qty <= 0:
            replaced[intent.intent_id] = None
            continue
        rebuilt = construction.build_order_intent(
            delta={"symbol": intent.symbol, "delta_qty": qty},
            prices=prices,
            run_id=intent.run_id,
            strategy_id=intent.strategy_id,
            bar_timestamp=intent.bar_timestamp,
            now=intent.timestamp,
        )
        rebuilt.metadata = {
            **(intent.metadata or {}),
            "budget_trimmed_from_qty": str(intent.qty),
        }
        replaced[intent.intent_id] = rebuilt
        kept_value += qty * price_of(intent.symbol)

    result: list[OrderIntent] = []
    for intent in intents:
        if intent.intent_id not in replaced:
            result.append(intent)
        elif (new := replaced[intent.intent_id]) is not None:
            result.append(new)
    return result, buys_value - kept_value


def _clamp_sells_to_account(
    intents: list[OrderIntent],
    account_positions: dict[str, Position],
    *,
    construction: Any,
    prices: dict[str, float],
) -> tuple[list[OrderIntent], dict[str, Decimal]]:
    sells_by_symbol: dict[str, list[OrderIntent]] = defaultdict(list)
    for intent in intents:
        if intent.side == Side.SELL:
            sells_by_symbol[intent.symbol].append(intent)

    replaced: dict[Any, OrderIntent | None] = {}
    clamped: dict[str, Decimal] = {}
    for symbol, sells in sells_by_symbol.items():
        held = account_positions.get(symbol)
        available = Decimal(held.quantity) if held is not None else Decimal("0")
        for intent in sorted(sells, key=lambda i: (i.strategy_id, str(i.intent_id))):
            qty = Decimal(intent.qty or 0)
            allowed = min(qty, max(available, Decimal("0")))
            available -= allowed
            if allowed == qty:
                continue
            clamped[symbol] = clamped.get(symbol, Decimal("0")) + (qty - allowed)
            if allowed <= 0:
                replaced[intent.intent_id] = None
                continue
            rebuilt = construction.build_order_intent(
                delta={"symbol": symbol, "delta_qty": -int(allowed)},
                prices=prices,
                run_id=intent.run_id,
                strategy_id=intent.strategy_id,
                bar_timestamp=intent.bar_timestamp,
                now=intent.timestamp,
            )
            rebuilt.metadata = {**(intent.metadata or {}), "clamped_from_qty": str(qty)}
            replaced[intent.intent_id] = rebuilt

    result: list[OrderIntent] = []
    for intent in intents:
        if intent.intent_id not in replaced:
            result.append(intent)
        elif (new := replaced[intent.intent_id]) is not None:
            result.append(new)
    return result, clamped


def _apply_state_event(
    deps: TradingCycleDependencies, strategy_id: str, event: StrategyEvent, now_utc: datetime
) -> None:
    """Best-effort runtime-state bookkeeping; never blocks other strategies' orders."""
    try:
        with SorUnitOfWork(deps.session) as uow:
            deps.execution_context.strategy_runtime_state_service.apply_event(
                uow=uow, strategy_id=strategy_id, event=event, now_utc=now_utc
            )
    except Exception as exc:
        logger.warning(
            "portfolio_evaluation.strategy_state_event_skipped",
            extra={"strategy_id": strategy_id, "event": str(event), "error": str(exc)},
        )
