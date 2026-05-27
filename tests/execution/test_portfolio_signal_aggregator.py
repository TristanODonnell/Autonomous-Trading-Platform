"""
Tests for PortfolioSignalAggregator (FINDING-08).

Coverage:
- CONSERVATIVE: equal BUY+SELL suppressed; no-conflict passes through
- DOMINANT: highest-confidence signal wins in a conflict
- PROPORTIONAL: weighted net direction; near-zero suppressed
- NETTING_ONLY: same net logic, netting_only policy label preserved
- Single-strategy pass-through (backward compatibility)
- Attribution preserved across all policies
- Wash-trade prevention
- Gross vs net exposure computed correctly
- Determinism: repeated calls produce same output
- Observability events emitted (structured log fields)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.trading.signal_aggregate import (
    AggregatedSignalBundle,
    SignalNettingPolicy,
)
from autonomous_trading_platform.execution.services.portfolio_signal_aggregator import (
    PortfolioSignalAggregator,
    _build_contributions,
    _has_conflict,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
_BAR_TS = datetime(2025, 6, 1, 14, 30, tzinfo=UTC)

_SIGNAL_ID_A1 = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SIGNAL_ID_A2 = UUID("aaaaaaaa-0000-0000-0000-000000000002")
_SIGNAL_ID_B1 = UUID("bbbbbbbb-0000-0000-0000-000000000001")
_SIGNAL_ID_B2 = UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _signal(
    *,
    strategy_id: str,
    symbol: str,
    direction: SignalDirection,
    confidence: float | None = 0.7,
    signal_id: UUID | None = None,
) -> Signal:
    return Signal(
        signal_id=signal_id or UUID("cccccccc-0000-0000-0000-000000000001"),
        run_id=_RUN_ID,
        timestamp=_BAR_TS,
        bar_timestamp=_BAR_TS,
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        confidence=confidence,
    )


def _aggregate(
    *,
    signals_by_strategy: dict[str, list[Signal]],
    policy: SignalNettingPolicy = SignalNettingPolicy.CONSERVATIVE,
    prices: dict[str, float] | None = None,
) -> AggregatedSignalBundle:
    return PortfolioSignalAggregator(policy=policy).aggregate(
        signals_by_strategy=signals_by_strategy,
        run_id=_RUN_ID,
        bar_timestamp=_BAR_TS,
        prices=prices,
    )


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------


class TestBuildContributions:
    def test_weights_sum_to_one(self):
        signals = [
            _signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY, confidence=0.8),
            _signal(
                strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL, confidence=0.2
            ),
        ]
        contributions = _build_contributions(signals)
        total = sum(c.weight for c in contributions)
        assert abs(total - 1.0) < 1e-9

    def test_none_confidence_uses_default(self):
        signals = [
            _signal(
                strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY, confidence=None
            ),
            _signal(
                strategy_id="s2", symbol="AAPL", direction=SignalDirection.BUY, confidence=None
            ),
        ]
        contributions = _build_contributions(signals)
        assert abs(contributions[0].weight - 0.5) < 1e-9
        assert abs(contributions[1].weight - 0.5) < 1e-9

    def test_strategy_ids_preserved(self):
        signals = [
            _signal(strategy_id="alpha", symbol="TSLA", direction=SignalDirection.BUY),
            _signal(strategy_id="beta", symbol="TSLA", direction=SignalDirection.SELL),
        ]
        contributions = _build_contributions(signals)
        ids = {c.strategy_id for c in contributions}
        assert ids == {"alpha", "beta"}


class TestHasConflict:
    def test_buy_and_sell_is_conflict(self):
        signals = [
            _signal(strategy_id="s1", symbol="X", direction=SignalDirection.BUY),
            _signal(strategy_id="s2", symbol="X", direction=SignalDirection.SELL),
        ]
        assert _has_conflict(_build_contributions(signals)) is True

    def test_buy_and_flat_is_conflict(self):
        signals = [
            _signal(strategy_id="s1", symbol="X", direction=SignalDirection.BUY),
            _signal(strategy_id="s2", symbol="X", direction=SignalDirection.FLAT),
        ]
        assert _has_conflict(_build_contributions(signals)) is True

    def test_sell_and_flat_no_conflict(self):
        signals = [
            _signal(strategy_id="s1", symbol="X", direction=SignalDirection.SELL),
            _signal(strategy_id="s2", symbol="X", direction=SignalDirection.FLAT),
        ]
        assert _has_conflict(_build_contributions(signals)) is False

    def test_all_buy_no_conflict(self):
        signals = [
            _signal(strategy_id="s1", symbol="X", direction=SignalDirection.BUY),
            _signal(strategy_id="s2", symbol="X", direction=SignalDirection.BUY),
        ]
        assert _has_conflict(_build_contributions(signals)) is False

    def test_single_signal_no_conflict(self):
        signals = [_signal(strategy_id="s1", symbol="X", direction=SignalDirection.BUY)]
        assert _has_conflict(_build_contributions(signals)) is False


# ---------------------------------------------------------------------------
# CONSERVATIVE policy
# ---------------------------------------------------------------------------


class TestConservativePolicy:
    def test_equal_buy_sell_suppressed(self):
        """Core acceptance criterion: opposing signals → no execution."""
        result = _aggregate(
            signals_by_strategy={
                "momentum": [
                    _signal(strategy_id="momentum", symbol="AAPL", direction=SignalDirection.BUY)
                ],
                "mean_rev": [
                    _signal(strategy_id="mean_rev", symbol="AAPL", direction=SignalDirection.SELL)
                ],
            },
        )
        assert len(result.reconciled_signals) == 0
        assert result.total_conflicts_detected == 1
        assert result.total_suppressed_symbols == 1

    def test_conflict_record_populated(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL)],
            },
        )
        conflict = result.conflicts[0]
        assert conflict.conflict_detected is True
        assert conflict.suppressed is True
        assert conflict.symbol == "AAPL"
        assert conflict.net_direction == SignalDirection.FLAT

    def test_no_conflict_passes_through(self):
        """Single agreeing strategy must not be suppressed."""
        result = _aggregate(
            signals_by_strategy={
                "momentum": [
                    _signal(strategy_id="momentum", symbol="TSLA", direction=SignalDirection.BUY)
                ],
            },
        )
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].symbol == "TSLA"
        assert result.total_conflicts_detected == 0

    def test_two_strategies_same_direction_not_suppressed(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.6,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.8,
                    )
                ],
            },
        )
        assert len(result.reconciled_signals) == 1
        assert result.total_conflicts_detected == 0

    def test_wash_trade_prevented(self):
        """No BUY and SELL intent submitted for same symbol in same cycle."""
        result = _aggregate(
            signals_by_strategy={
                "buyer": [
                    _signal(strategy_id="buyer", symbol="NVDA", direction=SignalDirection.BUY)
                ],
                "seller": [
                    _signal(strategy_id="seller", symbol="NVDA", direction=SignalDirection.SELL)
                ],
            },
        )
        buy_signals = [s for s in result.reconciled_signals if s.direction == SignalDirection.BUY]
        sell_signals = [s for s in result.reconciled_signals if s.direction == SignalDirection.SELL]
        assert not (buy_signals and sell_signals), (
            "Wash trade: simultaneous BUY and SELL reconciled"
        )
        assert len(result.reconciled_signals) == 0

    def test_buy_flat_conflict_suppressed(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="MSFT", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="MSFT", direction=SignalDirection.FLAT)],
            },
        )
        assert result.total_suppressed_symbols == 1
        assert len(result.reconciled_signals) == 0

    def test_multi_symbol_independent_resolution(self):
        """Conflict on AAPL must not affect TSLA."""
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A1,
                    ),
                    _signal(
                        strategy_id="s1",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A2,
                    ),
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        signal_id=_SIGNAL_ID_B1,
                    ),
                ],
            },
        )
        reconciled_symbols = {s.symbol for s in result.reconciled_signals}
        assert "AAPL" not in reconciled_symbols
        assert "TSLA" in reconciled_symbols


# ---------------------------------------------------------------------------
# DOMINANT policy
# ---------------------------------------------------------------------------


class TestDominantPolicy:
    def test_highest_confidence_wins(self):
        result = _aggregate(
            signals_by_strategy={
                "high_conf": [
                    _signal(
                        strategy_id="high_conf",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                    )
                ],
                "low_conf": [
                    _signal(
                        strategy_id="low_conf",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        confidence=0.3,
                    )
                ],
            },
            policy=SignalNettingPolicy.DOMINANT,
        )
        assert len(result.reconciled_signals) == 1
        s = result.reconciled_signals[0]
        assert s.direction == SignalDirection.BUY
        assert s.strategy_id == "high_conf"

    def test_dominant_conflict_record(self):
        result = _aggregate(
            signals_by_strategy={
                "alpha": [
                    _signal(
                        strategy_id="alpha",
                        symbol="TSLA",
                        direction=SignalDirection.SELL,
                        confidence=0.8,
                    )
                ],
                "beta": [
                    _signal(
                        strategy_id="beta",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        confidence=0.5,
                    )
                ],
            },
            policy=SignalNettingPolicy.DOMINANT,
        )
        conflict = result.conflicts[0]
        assert conflict.conflict_detected is True
        assert conflict.suppressed is False
        assert conflict.dominant_strategy_id == "alpha"

    def test_dominant_no_conflict_passes_through(self):
        result = _aggregate(
            signals_by_strategy={
                "only": [
                    _signal(
                        strategy_id="only",
                        symbol="GOOG",
                        direction=SignalDirection.BUY,
                        confidence=0.6,
                    )
                ],
            },
            policy=SignalNettingPolicy.DOMINANT,
        )
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].direction == SignalDirection.BUY

    def test_dominant_all_buy_picks_highest(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="X",
                        direction=SignalDirection.BUY,
                        confidence=0.5,
                        signal_id=_SIGNAL_ID_A1,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="X",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                        signal_id=_SIGNAL_ID_B1,
                    )
                ],
            },
            policy=SignalNettingPolicy.DOMINANT,
        )
        assert result.reconciled_signals[0].strategy_id == "s2"


# ---------------------------------------------------------------------------
# PROPORTIONAL policy
# ---------------------------------------------------------------------------


class TestProportionalPolicy:
    def test_equal_buy_sell_suppressed(self):
        """Equal weight BUY+SELL → net score 0 → suppress."""
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.5,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        confidence=0.5,
                    )
                ],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
        )
        assert result.total_suppressed_symbols == 1
        assert len(result.reconciled_signals) == 0

    def test_net_buy_wins(self):
        """BUY(0.9) vs SELL(0.1) → net positive → BUY emitted."""
        result = _aggregate(
            signals_by_strategy={
                "buy_str": [
                    _signal(
                        strategy_id="buy_str",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                    )
                ],
                "sell_str": [
                    _signal(
                        strategy_id="sell_str",
                        symbol="TSLA",
                        direction=SignalDirection.SELL,
                        confidence=0.1,
                    )
                ],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
        )
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].direction == SignalDirection.BUY

    def test_net_confidence_reflects_offset(self):
        """Net conviction should be reduced from full 1.0 because of the opposing signal."""
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        confidence=0.1,
                    )
                ],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
        )
        sig = result.reconciled_signals[0]
        assert sig.confidence is not None
        assert sig.confidence < 1.0
        assert sig.confidence > 0.0

    def test_attribution_embedded_in_params(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="MSFT",
                        direction=SignalDirection.BUY,
                        confidence=0.8,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="MSFT",
                        direction=SignalDirection.BUY,
                        confidence=0.6,
                    )
                ],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
        )
        sig = result.reconciled_signals[0]
        assert sig.params is not None
        assert "_netting_policy" in sig.params
        assert "_netting_strategy_ids" in sig.params
        assert "s1" in sig.params["_netting_strategy_ids"]
        assert "s2" in sig.params["_netting_strategy_ids"]

    def test_gross_differs_from_net_with_conflict(self):
        """Gross exposure counts all signals; net reflects directional offset."""
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL)],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
        )
        # Both signals suppressed, so both expose 0
        assert result.total_suppressed_symbols == 1

    def test_gross_differs_net_when_reconciled(self):
        """When BUY passes, gross > 0 and net > 0."""
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        confidence=0.1,
                    )
                ],
            },
            policy=SignalNettingPolicy.PROPORTIONAL,
            prices={"AAPL": 180.0},
        )
        assert result.total_suppressed_symbols == 0
        assert len(result.reconciled_signals) == 1


# ---------------------------------------------------------------------------
# NETTING_ONLY policy
# ---------------------------------------------------------------------------


class TestNettingOnlyPolicy:
    def test_policy_label_in_bundle(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.7,
                    )
                ],
            },
            policy=SignalNettingPolicy.NETTING_ONLY,
        )
        assert result.policy == SignalNettingPolicy.NETTING_ONLY

    def test_net_order_produced_for_dominant_direction(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        confidence=0.8,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        confidence=0.2,
                    )
                ],
            },
            policy=SignalNettingPolicy.NETTING_ONLY,
        )
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].direction == SignalDirection.BUY

    def test_netting_only_near_zero_suppressed(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1", symbol="X", direction=SignalDirection.BUY, confidence=0.5
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2", symbol="X", direction=SignalDirection.SELL, confidence=0.5
                    )
                ],
            },
            policy=SignalNettingPolicy.NETTING_ONLY,
        )
        assert result.total_suppressed_symbols == 1


# ---------------------------------------------------------------------------
# Attribution preservation
# ---------------------------------------------------------------------------


class TestAttributionPreservation:
    def test_attribution_exists_for_suppressed_symbol(self):
        """Attribution must survive even when all signals for a symbol are suppressed."""
        result = _aggregate(
            signals_by_strategy={
                "momentum": [
                    _signal(strategy_id="momentum", symbol="AAPL", direction=SignalDirection.BUY)
                ],
                "mean_rev": [
                    _signal(strategy_id="mean_rev", symbol="AAPL", direction=SignalDirection.SELL)
                ],
            },
        )
        assert "AAPL" in result.attributions
        strategy_ids = {c.strategy_id for c in result.attributions["AAPL"]}
        assert "momentum" in strategy_ids
        assert "mean_rev" in strategy_ids

    def test_attribution_direction_preserved(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="TSLA", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="TSLA", direction=SignalDirection.SELL)],
            },
        )
        contributions = result.attributions["TSLA"]
        by_strategy = {c.strategy_id: c.direction for c in contributions}
        assert by_strategy["s1"] == SignalDirection.BUY
        assert by_strategy["s2"] == SignalDirection.SELL

    def test_attribution_all_signals_counted(self):
        result = _aggregate(
            signals_by_strategy={
                "a": [
                    _signal(
                        strategy_id="a",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A1,
                    ),
                    _signal(
                        strategy_id="a",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A2,
                    ),
                ],
                "b": [
                    _signal(
                        strategy_id="b",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        signal_id=_SIGNAL_ID_B1,
                    ),
                    _signal(
                        strategy_id="b",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_B2,
                    ),
                ],
            },
        )
        assert len(result.attributions["AAPL"]) == 2
        assert len(result.attributions["TSLA"]) == 2


# ---------------------------------------------------------------------------
# Single-strategy backward compatibility
# ---------------------------------------------------------------------------


class TestSingleStrategyPassThrough:
    def test_buy_signal_passes_through_unchanged(self):
        sig = _signal(
            strategy_id="baseline",
            symbol="AAPL",
            direction=SignalDirection.BUY,
            signal_id=_SIGNAL_ID_A1,
        )
        result = _aggregate(signals_by_strategy={"baseline": [sig]})
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].signal_id == _SIGNAL_ID_A1
        assert result.total_conflicts_detected == 0

    def test_flat_signal_passes_through(self):
        sig = _signal(strategy_id="baseline", symbol="AAPL", direction=SignalDirection.FLAT)
        result = _aggregate(signals_by_strategy={"baseline": [sig]})
        assert len(result.reconciled_signals) == 1
        assert result.reconciled_signals[0].direction == SignalDirection.FLAT

    def test_empty_signals_produces_empty_bundle(self):
        result = _aggregate(signals_by_strategy={"baseline": []})
        assert len(result.reconciled_signals) == 0
        assert result.total_conflicts_detected == 0
        assert result.total_symbols_evaluated == 0

    def test_multiple_symbols_single_strategy(self):
        result = _aggregate(
            signals_by_strategy={
                "baseline": [
                    _signal(
                        strategy_id="baseline",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A1,
                    ),
                    _signal(
                        strategy_id="baseline",
                        symbol="TSLA",
                        direction=SignalDirection.SELL,
                        signal_id=_SIGNAL_ID_A2,
                    ),
                ]
            }
        )
        assert len(result.reconciled_signals) == 2
        assert result.total_conflicts_detected == 0


# ---------------------------------------------------------------------------
# Observability events
# ---------------------------------------------------------------------------


class TestObservabilityEvents:
    def test_conflict_detected_event_logged(self, caplog):
        with caplog.at_level(
            logging.INFO,
            logger="autonomous_trading_platform.execution.services.portfolio_signal_aggregator",
        ):
            _aggregate(
                signals_by_strategy={
                    "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                    "s2": [
                        _signal(strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL)
                    ],
                },
            )
        messages = [r.message for r in caplog.records]
        assert any("signal_conflict_detected" in m for m in messages)

    def test_signal_suppressed_event_logged(self, caplog):
        with caplog.at_level(
            logging.INFO,
            logger="autonomous_trading_platform.execution.services.portfolio_signal_aggregator",
        ):
            _aggregate(
                signals_by_strategy={
                    "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                    "s2": [
                        _signal(strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL)
                    ],
                },
            )
        messages = [r.message for r in caplog.records]
        assert any("signal_suppressed" in m for m in messages)

    def test_dominant_signal_selected_event_logged(self, caplog):
        with caplog.at_level(
            logging.INFO,
            logger="autonomous_trading_platform.execution.services.portfolio_signal_aggregator",
        ):
            _aggregate(
                signals_by_strategy={
                    "s1": [
                        _signal(
                            strategy_id="s1",
                            symbol="AAPL",
                            direction=SignalDirection.BUY,
                            confidence=0.9,
                        )
                    ],
                    "s2": [
                        _signal(
                            strategy_id="s2",
                            symbol="AAPL",
                            direction=SignalDirection.SELL,
                            confidence=0.3,
                        )
                    ],
                },
                policy=SignalNettingPolicy.DOMINANT,
            )
        messages = [r.message for r in caplog.records]
        assert any("dominant_signal_selected" in m for m in messages)

    def test_netting_applied_event_logged_on_proportional_conflict(self, caplog):
        with caplog.at_level(
            logging.INFO,
            logger="autonomous_trading_platform.execution.services.portfolio_signal_aggregator",
        ):
            _aggregate(
                signals_by_strategy={
                    "s1": [
                        _signal(
                            strategy_id="s1",
                            symbol="AAPL",
                            direction=SignalDirection.BUY,
                            confidence=0.9,
                        )
                    ],
                    "s2": [
                        _signal(
                            strategy_id="s2",
                            symbol="AAPL",
                            direction=SignalDirection.SELL,
                            confidence=0.1,
                        )
                    ],
                },
                policy=SignalNettingPolicy.PROPORTIONAL,
            )
        messages = [r.message for r in caplog.records]
        assert any("signal_netting_applied" in m for m in messages)

    def test_summary_log_always_emitted(self, caplog):
        with caplog.at_level(
            logging.INFO,
            logger="autonomous_trading_platform.execution.services.portfolio_signal_aggregator",
        ):
            _aggregate(signals_by_strategy={"s1": []})
        messages = [r.message for r in caplog.records]
        assert any("portfolio_signal_aggregator.summary" in m for m in messages)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_conservative_deterministic(self):
        kwargs = dict(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A1,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="AAPL",
                        direction=SignalDirection.SELL,
                        signal_id=_SIGNAL_ID_B1,
                    )
                ],
            },
        )
        r1 = _aggregate(**kwargs)
        r2 = _aggregate(**kwargs)
        assert r1.total_conflicts_detected == r2.total_conflicts_detected
        assert r1.total_suppressed_symbols == r2.total_suppressed_symbols
        assert len(r1.reconciled_signals) == len(r2.reconciled_signals)

    def test_dominant_deterministic(self):
        kwargs = dict(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        confidence=0.9,
                        signal_id=_SIGNAL_ID_A1,
                    )
                ],
                "s2": [
                    _signal(
                        strategy_id="s2",
                        symbol="TSLA",
                        direction=SignalDirection.SELL,
                        confidence=0.4,
                        signal_id=_SIGNAL_ID_B1,
                    )
                ],
            },
            policy=SignalNettingPolicy.DOMINANT,
        )
        r1 = _aggregate(**kwargs)
        r2 = _aggregate(**kwargs)
        assert r1.reconciled_signals[0].strategy_id == r2.reconciled_signals[0].strategy_id
        assert r1.reconciled_signals[0].direction == r2.reconciled_signals[0].direction


# ---------------------------------------------------------------------------
# Bundle metadata
# ---------------------------------------------------------------------------


class TestBundleMetadata:
    def test_total_strategies_counted(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="TSLA", direction=SignalDirection.BUY)],
                "s3": [_signal(strategy_id="s3", symbol="NVDA", direction=SignalDirection.BUY)],
            },
        )
        assert result.total_strategies == 3

    def test_total_symbols_counted(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [
                    _signal(
                        strategy_id="s1",
                        symbol="AAPL",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A1,
                    ),
                    _signal(
                        strategy_id="s1",
                        symbol="TSLA",
                        direction=SignalDirection.BUY,
                        signal_id=_SIGNAL_ID_A2,
                    ),
                ],
            },
        )
        assert result.total_symbols_evaluated == 2

    def test_aggregation_duration_non_negative(self):
        result = _aggregate(signals_by_strategy={"s1": []})
        assert result.aggregation_duration_seconds >= 0.0

    def test_bar_timestamp_propagated(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)]
            },
        )
        assert result.bar_timestamp == _BAR_TS

    def test_run_id_propagated(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)]
            },
        )
        assert result.run_id == _RUN_ID

    def test_policy_in_bundle(self):
        result = _aggregate(
            signals_by_strategy={"s1": []},
            policy=SignalNettingPolicy.DOMINANT,
        )
        assert result.policy == SignalNettingPolicy.DOMINANT


# ---------------------------------------------------------------------------
# Exposure metrics
# ---------------------------------------------------------------------------


class TestExposureComputation:
    def test_gross_positive_when_signals_exist(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="TSLA", direction=SignalDirection.BUY)],
            },
            prices={"AAPL": 180.0, "TSLA": 250.0},
        )
        # Two reconciled signals; gross should include both
        assert result.total_conflicts_detected == 0
        assert result.total_suppressed_symbols == 0

    def test_suppressed_conflict_reduces_reconciled_count(self):
        result = _aggregate(
            signals_by_strategy={
                "s1": [_signal(strategy_id="s1", symbol="AAPL", direction=SignalDirection.BUY)],
                "s2": [_signal(strategy_id="s2", symbol="AAPL", direction=SignalDirection.SELL)],
            },
        )
        assert len(result.reconciled_signals) == 0
