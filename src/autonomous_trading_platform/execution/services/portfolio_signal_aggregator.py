# autonomous_trading_platform/execution/services/portfolio_signal_aggregator.py
from __future__ import annotations

import uuid
from collections import defaultdict
from time import perf_counter
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.trading.signal_aggregate import (
    AggregatedSignalBundle,
    SignalAggregationConflict,
    SignalNettingPolicy,
    StrategySignalContribution,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_signal_aggregation_duration_seconds,
    ratp_signal_conflicts_total,
    ratp_signal_gross_exposure,
    ratp_signal_net_exposure,
    ratp_signal_netting_suppressed_total,
)

logger = get_logger(__name__)

_DEFAULT_CONFIDENCE = 0.5
# Minimum absolute net score required to emit an order; below this the symbol is suppressed.
_DEFAULT_NEAR_ZERO_THRESHOLD = 0.10

# Policy groupings for dispatch
_CONSERVATIVE_POLICIES = {SignalNettingPolicy.CONSERVATIVE, SignalNettingPolicy.SUPPRESS_CONFLICTS}
_DOMINANT_POLICIES = {SignalNettingPolicy.DOMINANT, SignalNettingPolicy.DOMINANT_SIGNAL}
_PROPORTIONAL_POLICIES = {
    SignalNettingPolicy.PROPORTIONAL,
    SignalNettingPolicy.NETTING_ONLY,
    SignalNettingPolicy.NET,
    SignalNettingPolicy.ALLOCATION_WEIGHTED,
    SignalNettingPolicy.CONFIDENCE_WEIGHTED,
}


def _raw_confidence(signal: Signal) -> float:
    return signal.confidence if signal.confidence is not None else _DEFAULT_CONFIDENCE


def _direction_score(direction: SignalDirection) -> float:
    return 1.0 if direction == SignalDirection.BUY else -1.0


def _build_contributions(
    symbol_signals: list[Signal],
    allocation_weights: dict[str, float] | None = None,
    confidence_only: bool = False,
) -> list[StrategySignalContribution]:
    """
    Build normalized contribution objects for each strategy signal.

    Weight computation:
    - allocation_weights provided → use allocation weight (ALLOCATION_WEIGHTED policy)
    - confidence_only=True → use raw confidence score (CONFIDENCE_WEIGHTED policy)
    - default → confidence-normalized equal across strategies
    """
    if allocation_weights is not None:
        raw_weights = [
            allocation_weights.get(s.strategy_id, _DEFAULT_CONFIDENCE) for s in symbol_signals
        ]
    elif confidence_only:
        raw_weights = [_raw_confidence(s) for s in symbol_signals]
    else:
        raw_weights = [_raw_confidence(s) for s in symbol_signals]

    total_weight = sum(raw_weights)
    if total_weight == 0.0:
        total_weight = len(symbol_signals) * _DEFAULT_CONFIDENCE

    return [
        StrategySignalContribution(
            strategy_id=s.strategy_id,
            signal_id=s.signal_id,
            direction=s.direction,
            confidence=s.confidence,
            weight=raw_weights[i] / total_weight,
        )
        for i, s in enumerate(symbol_signals)
    ]


def _has_conflict(contributions: list[StrategySignalContribution]) -> bool:
    """True when at least one BUY and at least one SELL/FLAT are present."""
    directions = {c.direction for c in contributions}
    return (SignalDirection.BUY in directions) and bool(
        directions & {SignalDirection.SELL, SignalDirection.FLAT}
    )


def _net_score_to_direction(
    net_score: float,
    threshold: float,
) -> SignalDirection | None:
    """Map weighted net score to a direction, or None for near-zero (suppress)."""
    if net_score > threshold:
        return SignalDirection.BUY
    if net_score < -threshold:
        return SignalDirection.FLAT
    return None


def _select_primary_signal(symbol_signals: list[Signal]) -> Signal | None:
    non_flat = [s for s in symbol_signals if s.direction != SignalDirection.FLAT]
    pool = non_flat or symbol_signals
    return max(pool, key=_raw_confidence, default=None)


def _emit_event(
    event_type: str,
    symbol: str,
    contributions: list[StrategySignalContribution],
    policy: SignalNettingPolicy,
    **extra,
) -> None:
    logger.info(
        f"portfolio_signal_aggregator.{event_type.lower()}",
        extra={
            "event_type": event_type,
            "symbol": symbol,
            "policy": policy.value,
            "contributing_strategies": [c.strategy_id for c in contributions],
            "directions": {c.strategy_id: c.direction.value for c in contributions},
            **extra,
        },
    )


class PortfolioSignalAggregator:
    """
    Portfolio-level signal aggregation layer.

    Sits between strategy evaluation and order intent generation. Receives
    signals keyed by strategy_id and produces a reconciled list of signals
    that eliminates wash trades, resolves conflicting directional intent, and
    preserves per-strategy attribution for governance and performance review.

    Policy variants:
    - CONSERVATIVE / SUPPRESS_CONFLICTS: suppress the entire symbol on any directional conflict
    - DOMINANT / DOMINANT_SIGNAL:        highest-conviction strategy wins; opposing suppressed
    - PROPORTIONAL / NETTING_ONLY / NET: weighted net direction; near-zero suppressed
    - ALLOCATION_WEIGHTED:               weight by external allocation weight per strategy
    - CONFIDENCE_WEIGHTED:               weight purely by per-signal confidence scores
    """

    def __init__(
        self,
        policy: SignalNettingPolicy = SignalNettingPolicy.CONSERVATIVE,
        near_zero_threshold: float = _DEFAULT_NEAR_ZERO_THRESHOLD,
    ) -> None:
        self.policy = policy
        self._threshold = near_zero_threshold

    def aggregate(
        self,
        signals_by_strategy: dict[str, list[Signal]],
        run_id: UUID,
        bar_timestamp: UTCDateTime,
        prices: dict[str, float] | None = None,
        allocation_weights: dict[str, float] | None = None,
    ) -> AggregatedSignalBundle:
        """
        Aggregate cross-strategy signals into a reconciled bundle.

        Args:
            signals_by_strategy: Raw signals keyed by strategy_id.
            run_id: Correlation ID for the current evaluation cycle.
            bar_timestamp: Bar timestamp these signals were generated for.
            prices: Optional symbol→price map for notional exposure calculation.
            allocation_weights: Optional strategy→weight map for ALLOCATION_WEIGHTED policy.
        """
        start = perf_counter()

        by_symbol: dict[str, list[Signal]] = defaultdict(list)
        for signals in signals_by_strategy.values():
            for signal in signals:
                by_symbol[signal.symbol].append(signal)

        reconciled_signals: list[Signal] = []
        conflicts: list[SignalAggregationConflict] = []
        attributions: dict[str, list[StrategySignalContribution]] = {}
        total_conflicts = 0
        total_suppressed = 0

        for symbol in sorted(by_symbol):
            symbol_signals = by_symbol[symbol]
            contributions = _build_contributions(
                symbol_signals,
                allocation_weights=allocation_weights
                if self.policy == SignalNettingPolicy.ALLOCATION_WEIGHTED
                else None,
                confidence_only=self.policy == SignalNettingPolicy.CONFIDENCE_WEIGHTED,
            )
            attributions[symbol] = contributions

            conflict_detected = _has_conflict(contributions)
            if conflict_detected:
                total_conflicts += 1
                _emit_event(
                    "SIGNAL_CONFLICT_DETECTED",
                    symbol,
                    contributions,
                    self.policy,
                )

            result_signal, conflict_record = self._dispatch_policy(
                symbol=symbol,
                contributions=contributions,
                symbol_signals=symbol_signals,
                conflict_detected=conflict_detected,
                bar_timestamp=bar_timestamp,
                run_id=run_id,
            )

            conflicts.append(conflict_record)

            if result_signal is None:
                total_suppressed += 1
                _emit_event("SIGNAL_SUPPRESSED", symbol, contributions, self.policy)
            else:
                reconciled_signals.append(result_signal)
                if conflict_detected:
                    _emit_event("SIGNAL_NETTING_APPLIED", symbol, contributions, self.policy)

        gross_exposure, net_exposure = _compute_signal_exposure(reconciled_signals, prices)
        duration = perf_counter() - start

        attrs: dict[str, str] = {"policy": self.policy.value}
        if total_conflicts > 0:
            ratp_signal_conflicts_total.add(total_conflicts, attrs)
        if total_suppressed > 0:
            ratp_signal_netting_suppressed_total.add(total_suppressed, attrs)
        ratp_signal_gross_exposure.record(gross_exposure, attrs)
        ratp_signal_net_exposure.record(net_exposure, attrs)
        ratp_signal_aggregation_duration_seconds.record(duration, attrs)

        logger.info(
            "portfolio_signal_aggregator.summary",
            extra={
                "policy": self.policy.value,
                "total_strategies": len(signals_by_strategy),
                "total_symbols": len(by_symbol),
                "total_conflicts": total_conflicts,
                "total_suppressed": total_suppressed,
                "reconciled_count": len(reconciled_signals),
                "gross_exposure": gross_exposure,
                "net_exposure": net_exposure,
                "duration_seconds": round(duration, 6),
            },
        )

        return AggregatedSignalBundle(
            bar_timestamp=bar_timestamp,
            run_id=run_id,
            policy=self.policy,
            reconciled_signals=reconciled_signals,
            conflicts=conflicts,
            attributions=attributions,
            total_strategies=len(signals_by_strategy),
            total_symbols_evaluated=len(by_symbol),
            total_conflicts_detected=total_conflicts,
            total_suppressed_symbols=total_suppressed,
            aggregation_duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Policy dispatch
    # ------------------------------------------------------------------

    def _dispatch_policy(
        self,
        *,
        symbol: str,
        contributions: list[StrategySignalContribution],
        symbol_signals: list[Signal],
        conflict_detected: bool,
        bar_timestamp: UTCDateTime,
        run_id: UUID,
    ) -> tuple[Signal | None, SignalAggregationConflict]:
        if self.policy in _CONSERVATIVE_POLICIES:
            return self._conservative(symbol, contributions, symbol_signals, conflict_detected)
        if self.policy in _DOMINANT_POLICIES:
            return self._dominant(symbol, contributions, symbol_signals, conflict_detected)
        if self.policy in _PROPORTIONAL_POLICIES:
            return self._proportional(
                symbol, contributions, symbol_signals, conflict_detected, bar_timestamp, run_id
            )
        return self._conservative(symbol, contributions, symbol_signals, conflict_detected)

    # ------------------------------------------------------------------
    # CONSERVATIVE / SUPPRESS_CONFLICTS
    # ------------------------------------------------------------------

    def _conservative(
        self,
        symbol: str,
        contributions: list[StrategySignalContribution],
        symbol_signals: list[Signal],
        conflict_detected: bool,
    ) -> tuple[Signal | None, SignalAggregationConflict]:
        if conflict_detected:
            return None, SignalAggregationConflict(
                symbol=symbol,
                policy_applied=self.policy,
                contributions=contributions,
                net_direction=SignalDirection.FLAT,
                conflict_detected=True,
                suppressed=True,
                dominant_strategy_id=None,
            )

        selected = _select_primary_signal(symbol_signals)
        net_dir = selected.direction if selected else SignalDirection.FLAT
        return selected, SignalAggregationConflict(
            symbol=symbol,
            policy_applied=self.policy,
            contributions=contributions,
            net_direction=net_dir,
            conflict_detected=False,
            suppressed=False,
            dominant_strategy_id=selected.strategy_id if selected else None,
        )

    # ------------------------------------------------------------------
    # DOMINANT / DOMINANT_SIGNAL
    # ------------------------------------------------------------------

    def _dominant(
        self,
        symbol: str,
        contributions: list[StrategySignalContribution],
        symbol_signals: list[Signal],
        conflict_detected: bool,
    ) -> tuple[Signal | None, SignalAggregationConflict]:
        best = max(symbol_signals, key=_raw_confidence)

        if conflict_detected:
            _emit_event(
                "DOMINANT_SIGNAL_SELECTED",
                symbol,
                contributions,
                self.policy,
                dominant_strategy_id=best.strategy_id,
                dominant_confidence=_raw_confidence(best),
            )

        return best, SignalAggregationConflict(
            symbol=symbol,
            policy_applied=self.policy,
            contributions=contributions,
            net_direction=best.direction,
            conflict_detected=conflict_detected,
            suppressed=False,
            dominant_strategy_id=best.strategy_id,
        )

    # ------------------------------------------------------------------
    # PROPORTIONAL / NETTING_ONLY / NET / ALLOCATION_WEIGHTED / CONFIDENCE_WEIGHTED
    # ------------------------------------------------------------------

    def _proportional(
        self,
        symbol: str,
        contributions: list[StrategySignalContribution],
        symbol_signals: list[Signal],
        conflict_detected: bool,
        bar_timestamp: UTCDateTime,
        run_id: UUID,
    ) -> tuple[Signal | None, SignalAggregationConflict]:
        net_score = sum(c.weight * _direction_score(c.direction) for c in contributions)
        net_dir = _net_score_to_direction(net_score, self._threshold)

        if net_dir is None:
            return None, SignalAggregationConflict(
                symbol=symbol,
                policy_applied=self.policy,
                contributions=contributions,
                net_direction=SignalDirection.FLAT,
                conflict_detected=conflict_detected,
                suppressed=True,
                dominant_strategy_id=None,
            )

        # Identify the primary contributing strategy matching the net direction.
        dominant_contribution = max(
            (c for c in contributions if c.direction == net_dir),
            key=lambda c: c.weight,
            default=contributions[0],
        )
        primary_signal = next(
            (s for s in symbol_signals if s.signal_id == dominant_contribution.signal_id),
            symbol_signals[0],
        )

        attribution_params: dict[str, object] = {
            "_netting_policy": self.policy.value,
            "_netting_net_score": round(net_score, 4),
            "_netting_strategy_ids": [c.strategy_id for c in contributions],
            "_netting_directions": {c.strategy_id: c.direction.value for c in contributions},
        }

        reconciled = Signal(
            signal_id=uuid.uuid4(),
            run_id=run_id,
            timestamp=primary_signal.timestamp,
            bar_timestamp=bar_timestamp,
            strategy_id=primary_signal.strategy_id,
            symbol=symbol,
            direction=net_dir,
            confidence=min(abs(net_score), 1.0),
            target_position=primary_signal.target_position,
            params={**(primary_signal.params or {}), **attribution_params},
        )

        return reconciled, SignalAggregationConflict(
            symbol=symbol,
            policy_applied=self.policy,
            contributions=contributions,
            net_direction=net_dir,
            conflict_detected=conflict_detected,
            suppressed=False,
            dominant_strategy_id=dominant_contribution.strategy_id,
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _compute_signal_exposure(
    signals: list[Signal],
    prices: dict[str, float] | None,
) -> tuple[float, float]:
    """
    Return (gross_exposure, net_exposure).

    When prices are available: USD notional.
    When prices are absent: directional signal counts.
    """
    if not signals:
        return 0.0, 0.0

    if prices:
        gross = 0.0
        net = 0.0
        for s in signals:
            p = prices.get(s.symbol, 0.0)
            sign = 1.0 if s.direction == SignalDirection.BUY else -1.0
            gross += p
            net += sign * p
        return gross, net

    buy_count = sum(1 for s in signals if s.direction == SignalDirection.BUY)
    sell_count = len(signals) - buy_count
    return float(len(signals)), float(buy_count - sell_count)
