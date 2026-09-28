# autonomous_trading_platform/scheduler/jobs/on_deck_shadow.py
"""
On-deck shadow trading (portfolio rotation step 2).

Every ON_DECK strategy goes through the same flow as an active one — evaluation,
sizing against its own (shadow) sleeve and budget, pre-trade risk checks, order
throttle — but its orders are filled in simulation into a shadow sleeve instead
of being sent to the broker. Nothing here produces a broker order or an
order_intents row, and shadow sleeves never enter the real sleeve/account
invariant.

Checks mirror the real path using the safety layer's own classes:
  - pre-trade risk: the real per-order and portfolio symbol checks, with the
    shadow sleeve standing in for the account (real total equity);
  - throttle: the real per-bar / per-hour / repeat limits, counted per strategy.
    As in real submission, a throttle rejection stops that strategy's remaining
    orders for the cycle.
Blocked orders are not filled; they are counted on the strategy's shadow snapshot.

Known differences from real trading: shadow orders do not compete with actives
for account-wide throttle slots, and shadow sleeves do not cross internally.

Shadow sleeves whose owner is no longer ON_DECK (promoted, dropped, ineligible)
are closed at the cycle price, so a later return to on-deck starts flat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import SleeveBook
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.shadow_fill_service import ShadowFillService
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.safety.errors import SafetyError
from autonomous_trading_platform.safety.readers.order_activity_reader import (
    StubOrderActivityReader,
)
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import (
    PositionAwareRiskStateReader,
)
from autonomous_trading_platform.safety.services.order_throttle_service import OrderThrottleService
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    StrategyRuntime,
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import EvaluateStrategyJob

logger = get_logger(__name__)


@dataclass
class ShadowStrategyOutcome:
    strategy_id: str
    # evaluated | bars_not_ready | failed | disabled
    status: str
    signal_count: int = 0
    intent_count: int = 0
    filled_count: int = 0
    # Orders filled for less than their quantity (volume participation cap).
    partial_count: int = 0
    # Orders with no fill at all (no bar/price, or zero-volume bar).
    unfilled_count: int = 0
    # Orders dropped by the pre-trade risk check or the throttle.
    blocked_count: int = 0
    error: str | None = None


@dataclass
class ShadowCycleResult:
    outcomes: list[ShadowStrategyOutcome] = field(default_factory=list)
    # Strategies whose shadow sleeves were closed because they left on-deck.
    liquidated: list[str] = field(default_factory=list)

    @property
    def blocked_count(self) -> int:
        return sum(o.blocked_count for o in self.outcomes)


def run_on_deck_shadow(
    *,
    now_utc: datetime,
    deps: TradingCycleDependencies,
    manifest: RunManifest,
    fetch_prices: Any,
    fetch_recent_closes: Any,
    vol_lookback_bars: int,
) -> ShadowCycleResult:
    session = deps.session
    broker_client = deps.execution_context.broker_client
    construction = deps.execution_context.portfolio_construction_service
    ledger = StrategySleeveLedgerService(book=SleeveBook.SHADOW)
    total_capital = Decimal(str(deps.portfolio_engine.total_capital))
    runtimes = [r for r in deps.strategy_runtimes or [] if r.status == MembershipStatus.ON_DECK]
    result = ShadowCycleResult()

    with SorUnitOfWork(session) as uow:
        sleeves = ledger.all_positions(uow)
        enabled = {
            runtime.strategy_id: uow.strategy_control_states.is_enabled(runtime.strategy_id)
            for runtime in runtimes
        }

    # 1. Evaluate.
    signals_by_strategy: dict[str, list[Signal]] = {}
    bar_by_strategy: dict[str, datetime] = {}
    outcomes: dict[str, ShadowStrategyOutcome] = {}
    for runtime in runtimes:
        sid = runtime.strategy_id
        if not enabled[sid] or runtime.strategy_context is None:
            outcomes[sid] = ShadowStrategyOutcome(sid, "disabled")
            continue
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
            logger.warning(
                "on_deck_shadow.strategy_evaluation_failed",
                extra={"strategy_id": sid, "run_id": str(manifest.run_id), "error": str(exc)},
            )
            outcomes[sid] = ShadowStrategyOutcome(sid, "failed", error=str(exc))
            continue
        if not job_result.evaluated:
            outcomes[sid] = ShadowStrategyOutcome(sid, "bars_not_ready")
            continue
        signals_by_strategy[sid] = list(job_result.signals)
        if job_result.target_bar_timestamp is not None:
            bar_by_strategy[sid] = job_result.target_bar_timestamp

    # 2. Prices for every signal and every shadow holding (incl. sleeves being closed).
    symbols: set[str] = {s.symbol for signals in signals_by_strategy.values() for s in signals}
    for held in sleeves.values():
        symbols.update(held)
    prices: dict[str, float] = fetch_prices(broker_client, sorted(symbols)) if symbols else {}
    fallback_bar = manifest.bar_timestamp or now_utc
    fill_service = ShadowFillService.for_broker(broker_client, session=session, timestamp=now_utc)

    # 3. Size, check, fill and book each evaluated strategy.
    for runtime in runtimes:
        sid = runtime.strategy_id
        if sid not in signals_by_strategy:
            continue
        signals = signals_by_strategy[sid]
        try:
            outcome = _shadow_trade(
                runtime=runtime,
                signals=signals,
                sleeve=sleeves.get(sid, {}),
                prices=prices,
                bar_timestamp=bar_by_strategy.get(sid, fallback_bar),
                now_utc=now_utc,
                deps=deps,
                manifest=manifest,
                construction=construction,
                ledger=ledger,
                fill_service=fill_service,
                total_capital=total_capital,
                fetch_recent_closes=fetch_recent_closes,
                vol_lookback_bars=vol_lookback_bars,
            )
        except Exception as exc:
            session.rollback()
            logger.warning(
                "on_deck_shadow.strategy_failed",
                extra={"strategy_id": sid, "run_id": str(manifest.run_id), "error": str(exc)},
            )
            outcome = ShadowStrategyOutcome(
                sid, "failed", signal_count=len(signals), error=str(exc)
            )
        outcomes[sid] = outcome

    # 4. Close shadow sleeves of strategies that are no longer on-deck.
    on_deck_ids = {runtime.strategy_id for runtime in runtimes}
    with SorUnitOfWork(session) as uow:
        for sid in sorted(set(sleeves) - on_deck_ids):
            ledger.liquidate(
                uow, strategy_id=sid, prices=prices, timestamp=now_utc, run_id=manifest.run_id
            )
            result.liquidated.append(sid)

    # 5. Value every on-deck sleeve (per-strategy shadow equity curve).
    with SorUnitOfWork(session) as uow:
        for runtime in runtimes:
            sid = runtime.strategy_id
            ledger.snapshot(
                uow,
                strategy_id=sid,
                prices=prices,
                timestamp=now_utc,
                run_id=manifest.run_id,
                allocated_capital=Decimal(str(runtime.budget_pct)) * total_capital,
                blocked_order_count=outcomes[sid].blocked_count if sid in outcomes else 0,
            )

    result.outcomes = [outcomes[r.strategy_id] for r in runtimes if r.strategy_id in outcomes]
    logger.info(
        "on_deck_shadow.completed",
        extra={
            "run_id": str(manifest.run_id),
            "outcomes": {o.strategy_id: o.status for o in result.outcomes},
            "filled": {o.strategy_id: o.filled_count for o in result.outcomes},
            "blocked": {o.strategy_id: o.blocked_count for o in result.outcomes},
            "liquidated": result.liquidated,
        },
    )
    return result


def _shadow_trade(
    *,
    runtime: StrategyRuntime,
    signals: list[Signal],
    sleeve: dict[str, Any],
    prices: dict[str, float],
    bar_timestamp: datetime,
    now_utc: datetime,
    deps: TradingCycleDependencies,
    manifest: RunManifest,
    construction: Any,
    ledger: StrategySleeveLedgerService,
    fill_service: ShadowFillService,
    total_capital: Decimal,
    fetch_recent_closes: Any,
    vol_lookback_bars: int,
) -> ShadowStrategyOutcome:
    # Imported here: portfolio_evaluation imports this module.
    from autonomous_trading_platform.scheduler.jobs.portfolio_evaluation import (
        _cap_buys_to_budget,
    )

    sid = runtime.strategy_id
    outcome = ShadowStrategyOutcome(sid, "evaluated", signal_count=len(signals))
    positions = {
        symbol: Position(symbol=symbol, quantity=held.quantity, avg_cost=held.avg_cost)
        for symbol, held in sleeve.items()
    }
    if not signals and not positions:
        return outcome

    def on_rejected(intent: OrderIntent, exc: Exception) -> None:
        outcome.blocked_count += 1
        logger.warning(
            "on_deck_shadow.order_blocked_by_risk",
            extra={"strategy_id": sid, "symbol": intent.symbol, "error": str(exc)},
        )

    recent_closes = (
        fetch_recent_closes(
            strategy_context=runtime.strategy_context,
            symbols=sorted({s.symbol for s in signals}),
            bar_timestamp=bar_timestamp,
            lookback_bars=vol_lookback_bars,
        )
        if signals
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
            pre_trade_risk_service=_shadow_risk_service(
                deps, positions=positions, prices=prices, total_equity=total_capital
            ),
            on_order_rejected=on_rejected,
        )
    )
    budget_usd = Decimal(str(runtime.budget_pct)) * total_capital
    generated, _ = _cap_buys_to_budget(
        generated, positions, budget_usd=budget_usd, construction=construction, prices=prices
    )
    outcome.intent_count = len(generated)

    # Throttle, per strategy; like real submission, a rejection stops the rest.
    throttle = OrderThrottleService(deps.settings, StubOrderActivityReader())
    allowed: list[OrderIntent] = []
    for index, intent in enumerate(generated):
        try:
            throttle.assert_order_allowed_for_submission(
                order_intent=intent, now=now_utc, bar_timestamp=intent.bar_timestamp
            )
        except SafetyError as exc:
            outcome.blocked_count += len(generated) - index
            logger.warning(
                "on_deck_shadow.orders_blocked_by_throttle",
                extra={
                    "strategy_id": sid,
                    "blocked": len(generated) - index,
                    "error": str(exc),
                },
            )
            break
        allowed.append(intent)

    fills = fill_service.fill(allowed, prices=prices)
    with SorUnitOfWork(deps.session) as uow:
        for fill in fills.fills:
            ledger.apply_fill(uow, fill=fill, strategy_id=sid)
    filled_intents = {fill.intent_id for fill in fills.fills}
    outcome.filled_count = len(filled_intents)
    outcome.partial_count = fills.partial_count
    outcome.unfilled_count = sum(1 for i in allowed if i.intent_id not in filled_intents)
    return outcome


def _shadow_risk_service(
    deps: TradingCycleDependencies,
    *,
    positions: dict[str, Position],
    prices: dict[str, float],
    total_equity: Decimal,
) -> PreTradeRiskService:
    """The real pre-trade risk checks, with the shadow sleeve as the account."""
    exposures: dict[str, Decimal] = {}
    quantities: dict[str, Decimal] = {}
    for symbol, position in positions.items():
        quantity = Decimal(position.quantity)
        mark = prices.get(symbol)
        price = Decimal(str(mark)) if mark is not None else Decimal(position.avg_cost or 0)
        exposures[symbol] = abs(quantity * price)
        quantities[symbol] = quantity
    reader = PortfolioRiskStateReader(exposures, total_equity, symbol_quantities=quantities)
    return PreTradeRiskService(
        settings=deps.settings,
        risk_state_reader=PositionAwareRiskStateReader(reader),
        portfolio_risk_state_reader=reader,
    )
