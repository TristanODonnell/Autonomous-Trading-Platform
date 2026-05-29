# autonomous_trading_platform/application/services/portfolio_construction_pipeline.py
"""
Portfolio Construction Pipeline — Recommendation 6.5.

Two-phase pipeline that separates strategy signal generation from
portfolio-level order construction:

  Phase 1 (Collect):    strategy signals → SignalBatch
  Phase 2 (Aggregate):  SignalBatch → AggregatedSignalBundle + PortfolioSignal list
  Phase 3 (Constrain):  PortfolioSignals → SignalIntent list (portfolio constraints applied)
  Phase 4 (Generate):   SignalIntents → OrderIntent list (sizing + per-order risk check)

Observability events emitted at each phase boundary:
  PORTFOLIO_SIGNALS_COLLECTED
  SIGNAL_CONFLICT_DETECTED      (delegated to PortfolioSignalAggregator)
  SIGNAL_NETTING_APPLIED        (delegated to PortfolioSignalAggregator)
  PORTFOLIO_CONSTRAINT_APPLIED
  PORTFOLIO_ORDER_INTENTS_GENERATED
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from time import perf_counter
from uuid import UUID

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.portfolio_signal import (
    ConstraintApplicationStatus,
    PortfolioConstructionDiagnostics,
    PortfolioConstructionResult,
    PortfolioSignal,
    SignalBatch,
    SignalIntent,
)
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.trading.signal_aggregate import (
    AggregatedSignalBundle,
    StrategySignalContribution,
)
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.portfolio_signal_aggregator import (
    PortfolioSignalAggregator,
    _compute_signal_exposure,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_portfolio_construction_duration_seconds,
    ratp_portfolio_net_signal_exposure,
    ratp_portfolio_raw_gross_signal_exposure,
    ratp_signal_suppressed_notional,
)

logger = get_logger(__name__)


class PortfolioConstraintConfig:
    """
    Configuration for the portfolio-level constraint engine in Phase 3.

    These are coarse pre-sizing constraints applied at the signal level,
    before the per-order pre_trade_risk_service checks that occur during
    Phase 4 sizing.  They exist to reject obviously infeasible signals
    early and to emit observable constraint-violation events.
    """

    def __init__(
        self,
        max_new_positions_per_cycle: int | None = None,
        max_gross_signal_exposure_usd: float | None = None,
        max_active_symbols: int | None = None,
    ) -> None:
        self.max_new_positions_per_cycle = max_new_positions_per_cycle
        self.max_gross_signal_exposure_usd = max_gross_signal_exposure_usd
        self.max_active_symbols = max_active_symbols


class PortfolioConstructionPipeline:
    """
    Two-phase portfolio construction pipeline.

    Strategies produce signals.  The pipeline aggregates, nets, applies
    portfolio-level constraints, and generates final executable OrderIntents.

    Phase 1 — Collect
        Create a SignalBatch from all raw strategy signals.  Emits
        PORTFOLIO_SIGNALS_COLLECTED.

    Phase 2 — Aggregate
        Run PortfolioSignalAggregator to resolve cross-strategy conflicts
        and produce an AggregatedSignalBundle.  Each reconciled signal is
        wrapped in a PortfolioSignal preserving attribution.

    Phase 3 — Constrain
        Apply portfolio-level constraints (exposure limits, new position
        count, active symbol count) to each PortfolioSignal.  Signals that
        pass are marked APPLIED; violating signals are marked REJECTED.
        Emits PORTFOLIO_CONSTRAINT_APPLIED.

    Phase 4 — Generate
        Pass APPLIED SignalIntents back through PortfolioConstructionService
        (which handles sizing, scaling, and per-order pre_trade_risk checks)
        to produce final OrderIntents.  Emits PORTFOLIO_ORDER_INTENTS_GENERATED.
    """

    def __init__(
        self,
        signal_aggregator: PortfolioSignalAggregator,
        portfolio_construction_service: PortfolioConstructionService,
        constraint_config: PortfolioConstraintConfig | None = None,
    ) -> None:
        self._aggregator = signal_aggregator
        self._construction_service = portfolio_construction_service
        self._constraint_config = constraint_config or PortfolioConstraintConfig()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(
        self,
        signals_by_strategy: dict[str, list[Signal]],
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        positions: Mapping[str, int | Position],
        prices: dict[str, float],
        now: datetime,
        strategy_id: str = "portfolio",
        approval_status: GovernanceState | None = None,
        performance_tier: str | None = None,
        recent_closes: dict[str, list[float]] | None = None,
        realized_drawdown: float | None = None,
        allocation_weights: dict[str, float] | None = None,
        persist_artifacts: bool = False,
    ) -> PortfolioConstructionResult:
        """
        Execute the full two-phase portfolio construction pipeline.

        Args:
            signals_by_strategy: Raw signals keyed by strategy_id.
            run_id: Cycle correlation ID.
            bar_timestamp: Bar these signals were generated for.
            positions: Current broker positions (symbol → qty or Position).
            prices: Latest symbol prices.
            now: Wall-clock time for order timestamp and risk checks.
            strategy_id: Identifier used as the portfolio-level actor in order intents.
            approval_status: Governance approval state (passed to position sizer).
            performance_tier: Performance tier (passed to position sizer).
            recent_closes: Recent price history for volatility/Sharpe scaling.
            realized_drawdown: Current realized drawdown fraction.
            allocation_weights: Optional strategy→weight for ALLOCATION_WEIGHTED policy.
            persist_artifacts: If True, artifacts are saved via the repository (if wired).
        """
        pipeline_start = perf_counter()

        # Phase 1 — Collect
        signal_batch, raw_signals = self._phase1_collect(
            signals_by_strategy=signals_by_strategy,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            now=now,
        )

        # Phase 2 — Aggregate
        bundle, portfolio_signals = self._phase2_aggregate(
            raw_signals=raw_signals,
            signal_batch=signal_batch,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            prices=prices,
            allocation_weights=allocation_weights,
        )

        # Phase 3 — Constrain
        signal_intents = self._phase3_constrain(
            portfolio_signals=portfolio_signals,
            positions=positions,
            prices=prices,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            batch_id=signal_batch.batch_id,
        )

        # Phase 4 — Generate
        order_intents = self._phase4_generate(
            signal_intents=signal_intents,
            positions=positions,
            prices=prices,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            now=now,
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
            recent_closes=recent_closes,
            realized_drawdown=realized_drawdown,
        )

        construction_duration = perf_counter() - pipeline_start

        # Diagnostics
        diagnostics = self._build_diagnostics(
            signal_batch=signal_batch,
            bundle=bundle,
            portfolio_signals=portfolio_signals,
            signal_intents=signal_intents,
            order_intents=order_intents,
            prices=prices,
            constructed_at=now,
            construction_duration=construction_duration,
        )

        # Emit pipeline-level metrics
        attrs: dict[str, str] = {"policy": self._aggregator.policy.value}
        ratp_portfolio_raw_gross_signal_exposure.record(
            diagnostics.raw_gross_signal_exposure, attrs
        )
        ratp_portfolio_net_signal_exposure.record(diagnostics.net_signal_exposure, attrs)
        ratp_portfolio_construction_duration_seconds.record(construction_duration, attrs)
        if diagnostics.suppressed_notional_usd > 0:
            ratp_signal_suppressed_notional.record(diagnostics.suppressed_notional_usd, attrs)

        logger.info(
            "PORTFOLIO_ORDER_INTENTS_GENERATED",
            extra={
                "event_type": "PORTFOLIO_ORDER_INTENTS_GENERATED",
                "run_id": str(run_id),
                "batch_id": str(signal_batch.batch_id),
                "strategy_count": diagnostics.strategy_count,
                "symbol_count": diagnostics.symbol_count,
                "raw_signal_count": diagnostics.raw_signal_count,
                "netted_signal_count": diagnostics.netted_signal_count,
                "suppressed_count": diagnostics.suppressed_count,
                "conflict_count": diagnostics.conflict_count,
                "constraint_applied_count": diagnostics.constraint_applied_count,
                "constraint_rejected_count": diagnostics.constraint_rejected_count,
                "final_order_count": diagnostics.final_order_count,
                "construction_duration_seconds": round(construction_duration, 6),
            },
        )

        return PortfolioConstructionResult(
            signal_batch=signal_batch,
            aggregated_bundle=bundle,
            portfolio_signals=portfolio_signals,
            signal_intents=signal_intents,
            conflicts=bundle.conflicts,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Phase 1 — Collect
    # ------------------------------------------------------------------

    def _phase1_collect(
        self,
        signals_by_strategy: dict[str, list[Signal]],
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        now: datetime,
    ) -> tuple[SignalBatch, dict[str, list[Signal]]]:
        all_symbols: set[str] = set()
        raw_signal_count = 0
        for strategy_signals in signals_by_strategy.values():
            for s in strategy_signals:
                all_symbols.add(s.symbol)
                raw_signal_count += 1

        batch_id = uuid.uuid4()
        batch = SignalBatch(
            batch_id=batch_id,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            collected_at=bar_timestamp,
            strategy_count=len(signals_by_strategy),
            symbol_count=len(all_symbols),
            raw_signal_count=raw_signal_count,
            strategy_ids=sorted(signals_by_strategy.keys()),
        )

        logger.info(
            "PORTFOLIO_SIGNALS_COLLECTED",
            extra={
                "event_type": "PORTFOLIO_SIGNALS_COLLECTED",
                "batch_id": str(batch_id),
                "run_id": str(run_id),
                "strategy_count": len(signals_by_strategy),
                "symbol_count": len(all_symbols),
                "raw_signal_count": raw_signal_count,
            },
        )

        return batch, signals_by_strategy

    # ------------------------------------------------------------------
    # Phase 2 — Aggregate
    # ------------------------------------------------------------------

    def _phase2_aggregate(
        self,
        raw_signals: dict[str, list[Signal]],
        signal_batch: SignalBatch,
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        prices: dict[str, float] | None,
        allocation_weights: dict[str, float] | None,
    ) -> tuple[AggregatedSignalBundle, list[PortfolioSignal]]:
        bundle = self._aggregator.aggregate(
            signals_by_strategy=raw_signals,
            run_id=run_id,
            bar_timestamp=bar_timestamp,
            prices=prices,
            allocation_weights=allocation_weights,
        )

        portfolio_signals: list[PortfolioSignal] = []
        conflict_map = {c.symbol: c for c in bundle.conflicts}

        for signal in bundle.reconciled_signals:
            conflict = conflict_map.get(signal.symbol)
            attribution = bundle.attributions.get(signal.symbol, [])
            ps = PortfolioSignal(
                portfolio_signal_id=uuid.uuid4(),
                batch_id=signal_batch.batch_id,
                run_id=run_id,
                bar_timestamp=bar_timestamp,
                symbol=signal.symbol,
                net_direction=signal.direction,
                net_confidence=signal.confidence if signal.confidence is not None else 0.5,
                gross_buy_notional=_notional_for_direction(signal, prices, SignalDirection.BUY),
                gross_sell_notional=_notional_for_direction(signal, prices, SignalDirection.SELL),
                net_notional=_net_notional(signal, prices),
                contributing_strategy_ids=[c.strategy_id for c in attribution],
                conflict_detected=conflict.conflict_detected if conflict else False,
                attribution=attribution,
                netting_policy=self._aggregator.policy,
                constraint_status=ConstraintApplicationStatus.PENDING,
            )
            portfolio_signals.append(ps)

        # Include suppressed symbols as FLAT portfolio signals for full auditability
        reconciled_symbols = {s.symbol for s in bundle.reconciled_signals}
        for conflict in bundle.conflicts:
            if conflict.suppressed and conflict.symbol not in reconciled_symbols:
                attribution = bundle.attributions.get(conflict.symbol, [])
                ps = PortfolioSignal(
                    portfolio_signal_id=uuid.uuid4(),
                    batch_id=signal_batch.batch_id,
                    run_id=run_id,
                    bar_timestamp=bar_timestamp,
                    symbol=conflict.symbol,
                    net_direction=SignalDirection.FLAT,
                    net_confidence=0.0,
                    contributing_strategy_ids=[c.strategy_id for c in attribution],
                    conflict_detected=True,
                    attribution=attribution,
                    netting_policy=self._aggregator.policy,
                    constraint_status=ConstraintApplicationStatus.REJECTED,
                )
                portfolio_signals.append(ps)

        return bundle, portfolio_signals

    # ------------------------------------------------------------------
    # Phase 3 — Constrain
    # ------------------------------------------------------------------

    def _phase3_constrain(
        self,
        portfolio_signals: list[PortfolioSignal],
        positions: Mapping[str, int | Position],
        prices: dict[str, float],
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        batch_id: UUID,
    ) -> list[SignalIntent]:
        cfg = self._constraint_config
        current_position_symbols = _active_position_symbols(positions)
        new_positions_this_cycle = 0
        cumulative_gross_usd = 0.0
        intents: list[SignalIntent] = []

        for ps in portfolio_signals:
            # Already rejected at aggregation phase (suppressed conflicts)
            if ps.constraint_status == ConstraintApplicationStatus.REJECTED:
                intents.append(
                    _make_signal_intent(
                        ps,
                        batch_id,
                        run_id,
                        bar_timestamp,
                        rejected=True,
                        reasons=["signal_suppressed_by_netting"],
                    )
                )
                continue

            # SELL/FLAT signals: always pass through — exits are unconditionally allowed
            if ps.net_direction in (SignalDirection.SELL, SignalDirection.FLAT):
                intents.append(
                    _make_signal_intent(ps, batch_id, run_id, bar_timestamp, rejected=False)
                )
                continue

            # BUY signals — apply portfolio constraints
            constraints_triggered: list[str] = []
            is_new_position = ps.symbol not in current_position_symbols

            if is_new_position:
                if (
                    cfg.max_new_positions_per_cycle is not None
                    and new_positions_this_cycle >= cfg.max_new_positions_per_cycle
                ):
                    constraints_triggered.append("max_new_positions_per_cycle")

                if cfg.max_active_symbols is not None:
                    projected_active = len(current_position_symbols) + new_positions_this_cycle
                    if projected_active >= cfg.max_active_symbols:
                        constraints_triggered.append("max_active_symbols")

            signal_notional = prices.get(ps.symbol, 0.0)
            if (
                cfg.max_gross_signal_exposure_usd is not None
                and (cumulative_gross_usd + signal_notional) > cfg.max_gross_signal_exposure_usd
            ):
                constraints_triggered.append("max_gross_signal_exposure_usd")

            rejected = bool(constraints_triggered)

            if not rejected:
                if is_new_position:
                    new_positions_this_cycle += 1
                cumulative_gross_usd += signal_notional

            intents.append(
                _make_signal_intent(
                    ps,
                    batch_id,
                    run_id,
                    bar_timestamp,
                    rejected=rejected,
                    reasons=constraints_triggered,
                )
            )

        applied = sum(
            1 for i in intents if i.constraint_status == ConstraintApplicationStatus.APPLIED
        )
        rejected_count = sum(
            1 for i in intents if i.constraint_status == ConstraintApplicationStatus.REJECTED
        )

        logger.info(
            "PORTFOLIO_CONSTRAINT_APPLIED",
            extra={
                "event_type": "PORTFOLIO_CONSTRAINT_APPLIED",
                "run_id": str(run_id),
                "batch_id": str(batch_id),
                "signals_evaluated": len(portfolio_signals),
                "applied_count": applied,
                "rejected_count": rejected_count,
            },
        )

        return intents

    # ------------------------------------------------------------------
    # Phase 4 — Generate
    # ------------------------------------------------------------------

    def _phase4_generate(
        self,
        signal_intents: list[SignalIntent],
        positions: Mapping[str, int | Position],
        prices: dict[str, float],
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        now: datetime,
        strategy_id: str,
        approval_status: GovernanceState | None,
        performance_tier: str | None,
        recent_closes: dict[str, list[float]] | None,
        realized_drawdown: float | None,
    ) -> list[OrderIntent]:
        # Convert APPLIED signal intents back to Signal objects for PortfolioConstructionService
        signals: list[Signal] = []
        for intent in signal_intents:
            if intent.constraint_status != ConstraintApplicationStatus.APPLIED:
                continue
            signals.append(
                Signal(
                    signal_id=intent.intent_id,
                    run_id=intent.run_id,
                    timestamp=intent.bar_timestamp,
                    bar_timestamp=intent.bar_timestamp,
                    strategy_id=strategy_id,
                    symbol=intent.symbol,
                    direction=intent.direction,
                    confidence=intent.confidence,
                )
            )

        order_intents: list[OrderIntent] = []
        for order_intent in self._construction_service.generate_order_intents(
            signals=signals,
            positions=positions,
            prices=prices,
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            now=now,
            approval_status=approval_status,
            performance_tier=performance_tier,
            recent_closes=recent_closes,
            realized_drawdown=realized_drawdown,
        ):
            order_intents.append(order_intent)

        return order_intents

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _build_diagnostics(
        self,
        signal_batch: SignalBatch,
        bundle: AggregatedSignalBundle,
        portfolio_signals: list[PortfolioSignal],
        signal_intents: list[SignalIntent],
        order_intents: list[OrderIntent],
        prices: dict[str, float],
        constructed_at: datetime,
        construction_duration: float,
    ) -> PortfolioConstructionDiagnostics:
        suppressed_notional = sum(
            prices.get(ps.symbol, 0.0)
            for ps in portfolio_signals
            if ps.constraint_status == ConstraintApplicationStatus.REJECTED
            and ps.net_direction == SignalDirection.BUY
        )

        applied_intents = [
            i for i in signal_intents if i.constraint_status == ConstraintApplicationStatus.APPLIED
        ]
        rejected_intents = [
            i for i in signal_intents if i.constraint_status == ConstraintApplicationStatus.REJECTED
        ]

        # Exposure before constraints = all non-flat reconciled signals
        pre_constraint_signals = [
            ps for ps in portfolio_signals if ps.net_direction == SignalDirection.BUY
        ]
        gross_before, net_before = _compute_exposure_from_portfolio_signals(
            pre_constraint_signals, prices
        )

        # Exposure after constraints = only applied intents with BUY direction
        post_constraint_signals = [
            ps
            for ps in portfolio_signals
            if ps.net_direction == SignalDirection.BUY
            and ps.constraint_status == ConstraintApplicationStatus.APPLIED
        ]
        gross_after, net_after = _compute_exposure_from_portfolio_signals(
            post_constraint_signals, prices
        )

        raw_gross, raw_net = _compute_signal_exposure(bundle.reconciled_signals, prices)

        return PortfolioConstructionDiagnostics(
            batch_id=signal_batch.batch_id,
            run_id=signal_batch.run_id,
            bar_timestamp=signal_batch.bar_timestamp,
            constructed_at=signal_batch.bar_timestamp,
            netting_policy=self._aggregator.policy.value,
            strategy_count=signal_batch.strategy_count,
            symbol_count=signal_batch.symbol_count,
            raw_signal_count=signal_batch.raw_signal_count,
            netted_signal_count=len(bundle.reconciled_signals),
            suppressed_count=bundle.total_suppressed_symbols,
            suppressed_notional_usd=suppressed_notional,
            conflict_count=bundle.total_conflicts_detected,
            constraint_applied_count=len(applied_intents),
            constraint_rejected_count=len(rejected_intents),
            raw_gross_signal_exposure=raw_gross,
            net_signal_exposure=raw_net,
            exposure_before_constraints=gross_before,
            exposure_after_constraints=gross_after,
            final_order_count=len(order_intents),
            construction_duration_seconds=construction_duration,
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _notional_for_direction(
    signal: Signal,
    prices: dict[str, float] | None,
    direction: SignalDirection,
) -> float | None:
    if prices is None or signal.direction != direction:
        return None
    return prices.get(signal.symbol)


def _net_notional(signal: Signal, prices: dict[str, float] | None) -> float | None:
    if prices is None:
        return None
    p = prices.get(signal.symbol, 0.0)
    sign = 1.0 if signal.direction == SignalDirection.BUY else -1.0
    return sign * p


def _active_position_symbols(positions: Mapping[str, int | Position]) -> set[str]:
    active: set[str] = set()
    for sym, pos in positions.items():
        qty = int(pos.quantity) if hasattr(pos, "quantity") else int(pos)
        if qty != 0:
            active.add(sym)
    return active


def _make_signal_intent(
    ps: PortfolioSignal,
    batch_id: UUID,
    run_id: UUID,
    bar_timestamp: UTCDateTime,
    rejected: bool,
    reasons: list[str] | None = None,
) -> SignalIntent:
    status = (
        ConstraintApplicationStatus.REJECTED if rejected else ConstraintApplicationStatus.APPLIED
    )
    return SignalIntent(
        intent_id=uuid.uuid4(),
        portfolio_signal_id=ps.portfolio_signal_id,
        batch_id=batch_id,
        run_id=run_id,
        bar_timestamp=bar_timestamp,
        symbol=ps.symbol,
        direction=ps.net_direction,
        confidence=ps.net_confidence,
        constraint_status=status,
        constraints_triggered=reasons or [],
        attribution=ps.attribution,
    )


def _compute_exposure_from_portfolio_signals(
    signals: list[PortfolioSignal],
    prices: dict[str, float],
) -> tuple[float, float]:
    gross = 0.0
    net = 0.0
    for ps in signals:
        p = prices.get(ps.symbol, 0.0)
        sign = 1.0 if ps.net_direction == SignalDirection.BUY else -1.0
        gross += p
        net += sign * p
    return gross, net


def _contribution_strategy_ids(contributions: list[StrategySignalContribution]) -> list[str]:
    return [c.strategy_id for c in contributions]
