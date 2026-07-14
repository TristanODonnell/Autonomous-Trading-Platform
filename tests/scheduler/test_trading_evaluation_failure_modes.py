"""
Tests for trading evaluation job failure modes.

Covers:
- Bug #2: generate_order_intents is a lazy generator; exceptions inside it surface
  only when list() is called in run_trading_cycle, NOT when run_trading_evaluation_job
  returns. Each per-symbol error (SafetyError, KeyError, InsufficientCapitalError)
  must be swallowed gracefully, skipping only that symbol.
- Bug #3: _fetch_prices must be called with the union of signal_symbols and
  position_symbols so that exit deltas for held-but-unsignalled positions can be
  costed without KeyError in build_order_intent.
- Generator laziness: the generator object is returned before list() is called, so
  record_job_completed fires before any per-order exceptions are raised.
"""

from __future__ import annotations

import types
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import Side, SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_sizer import SizingResult
from autonomous_trading_platform.portfolio.exceptions import (
    AllocationDeniedError,
    InsufficientCapitalError,
    NoPolicyFoundError,
)
from autonomous_trading_platform.safety.errors import SafetyError

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_RUN_ID = UUID("00000000-0000-0000-0000-000000000099")
_STRATEGY_ID = "test-strategy"
_BAR_TS = datetime(2025, 3, 14, 15, 30, tzinfo=UTC)
_NOW = datetime(2025, 3, 14, 15, 30, 45, tzinfo=UTC)
_BASE_NOTIONAL = Decimal("10_000")


def _signal(symbol: str, direction: SignalDirection = SignalDirection.BUY) -> Signal:
    return Signal(
        signal_id=UUID("10000000-0000-0000-0000-000000000001"),
        run_id=_RUN_ID,
        timestamp=_BAR_TS,
        bar_timestamp=_BAR_TS,
        strategy_id=_STRATEGY_ID,
        symbol=symbol,
        direction=direction,
        confidence=1.0,
    )


def _position(symbol: str, qty: int = 10) -> Position:
    return Position(symbol=symbol, quantity=Decimal(qty))


class _FixedSizer:
    """Returns qty=10 for every symbol by default."""

    def __init__(self, raises_for: dict[str, type[Exception]] | None = None) -> None:
        self._raises_for: dict[str, type[Exception]] = raises_for or {}

    def compute_quantity(
        self,
        *,
        strategy_id: str,
        approval_status: object,
        symbol: str,
        current_price: object,
        performance_tier: str | None = None,
        combined_scalar: Decimal | None = None,
        realized_drawdown: float | None = None,
    ) -> SizingResult:
        exc_type = self._raises_for.get(symbol)
        if exc_type is not None:
            if exc_type is InsufficientCapitalError:
                raise exc_type(strategy_id, 1000.0, 500.0)
            if exc_type is AllocationDeniedError:
                raise exc_type(strategy_id, "test denial")
            if exc_type is NoPolicyFoundError:
                raise exc_type("approved_for_paper_trading", None)
            raise exc_type("generic sizer error")
        return SizingResult(
            quantity=10,
            base_notional=_BASE_NOTIONAL,
            final_notional=_BASE_NOTIONAL,
            combined_scalar=None,
            scaling_applied=False,
        )


class _RiskService:
    """Passes all orders; can be told to reject a specific symbol."""

    def __init__(self, reject_symbol: str | None = None) -> None:
        self._reject = reject_symbol
        self.allowed: list[str] = []

    def assert_order_allowed(self, order_intent: object, now: object) -> None:
        sym = order_intent.symbol  # type: ignore[attr-defined]
        if sym == self._reject:
            raise SafetyError(f"Pre-trade blocked: {sym}")
        self.allowed.append(sym)


class _RejectAllRiskService:
    def assert_order_allowed(self, order_intent: object, now: object) -> None:
        raise SafetyError("blocked by policy")


def _make_service(
    risk: object | None = None,
    sizer: object | None = None,
) -> PortfolioConstructionService:
    return PortfolioConstructionService(
        pre_trade_risk_service=risk or _RiskService(),  # type: ignore[arg-type]
        position_sizer=sizer or _FixedSizer(),  # type: ignore[arg-type]
    )


def _intents(
    service: PortfolioConstructionService,
    signals: list[Signal],
    positions: dict | None = None,
    prices: dict | None = None,
) -> list:
    positions = positions or {}
    # Default prices: every signal and position symbol gets a price
    if prices is None:
        all_symbols = {s.symbol for s in signals} | set(positions.keys())
        prices = {sym: 100.0 for sym in all_symbols}
    return list(
        service.generate_order_intents(
            signals=signals,
            positions=positions,
            prices=prices,
            run_id=_RUN_ID,
            strategy_id=_STRATEGY_ID,
            bar_timestamp=_BAR_TS,
            now=_NOW,
        )
    )


# ---------------------------------------------------------------------------
# Bug #2 — generator laziness and per-symbol exception handling
# ---------------------------------------------------------------------------


class TestGeneratorExceptionHandling:
    def test_generate_order_intents_returns_a_generator(self) -> None:
        """generate_order_intents must be lazy so record_job_completed fires before list()."""
        service = _make_service()
        result = service.generate_order_intents(
            signals=[_signal("AAPL")],
            positions={},
            prices={"AAPL": 150.0},
            run_id=_RUN_ID,
            strategy_id=_STRATEGY_ID,
            bar_timestamp=_BAR_TS,
            now=_NOW,
        )
        assert isinstance(result, types.GeneratorType), (
            "generate_order_intents must be a generator type, not a list. "
            "Converting to list() eagerly would fire exceptions inside run_trading_evaluation_job "
            "before record_job_completed, losing observability of the true failure point."
        )

    def test_safety_error_propagates_to_caller(self) -> None:
        """SafetyError from assert_order_allowed propagates out of the generator.

        Pre-trade risk rejections must reach the trading cycle manifest (see
        test_pre_trade_risk_rejection_blocks_order_submission), so this is not
        swallowed per-symbol — the whole evaluation halts. Symbols ordered before
        the offending one still yield, since the generator is lazy.
        """
        service = _make_service(risk=_RiskService(reject_symbol="NVDA"))
        gen = service.generate_order_intents(
            signals=[_signal("NVDA")],
            positions={},
            prices={"NVDA": 900.0},
            run_id=_RUN_ID,
            strategy_id=_STRATEGY_ID,
            bar_timestamp=_BAR_TS,
            now=_NOW,
        )
        with pytest.raises(SafetyError):
            list(gen)

    def test_missing_price_keyerror_skips_that_symbol(self) -> None:
        """KeyError from build_order_intent (missing price) skips that symbol only."""
        service = _make_service()
        # MSFT has no price entry → KeyError in build_order_intent
        results = _intents(
            service,
            [_signal("AAPL"), _signal("MSFT")],
            prices={"AAPL": 150.0},
        )

        symbols = {i.symbol for i in results}
        assert "AAPL" in symbols
        assert "MSFT" not in symbols

    def test_insufficient_capital_skips_that_symbol_others_proceed(self) -> None:
        """InsufficientCapitalError from position sizer skips that symbol (Bug #5)."""
        sizer = _FixedSizer(raises_for={"TSLA": InsufficientCapitalError})
        service = _make_service(sizer=sizer)

        results = _intents(service, [_signal("AAPL"), _signal("TSLA")])

        symbols = {i.symbol for i in results}
        assert "AAPL" in symbols
        assert "TSLA" not in symbols

    def test_allocation_denied_error_skips_that_symbol(self) -> None:
        sizer = _FixedSizer(raises_for={"MSFT": AllocationDeniedError})
        service = _make_service(sizer=sizer)

        results = _intents(service, [_signal("AAPL"), _signal("MSFT")])

        symbols = {i.symbol for i in results}
        assert "AAPL" in symbols
        assert "MSFT" not in symbols

    def test_no_policy_found_skips_that_symbol(self) -> None:
        sizer = _FixedSizer(raises_for={"AMZN": NoPolicyFoundError})
        service = _make_service(sizer=sizer)

        results = _intents(service, [_signal("AAPL"), _signal("AMZN")])

        symbols = {i.symbol for i in results}
        assert "AAPL" in symbols
        assert "AMZN" not in symbols

    def test_compute_target_positions_unexpected_exception_propagates(self) -> None:
        """An unexpected error in _compute_target_positions propagates out of the generator.

        Only the specific domain exceptions (AllocationDeniedError, NoPolicyFoundError,
        InsufficientCapitalError) are caught and turned into a per-symbol skip; anything
        else — including MissingPositionScalingDataError for live-mode fail-closed
        (see TestLiveModeFailClosed) — must propagate rather than being silently
        swallowed.
        """

        class _BrokenSizer:
            def compute_quantity(self, **_):
                raise RuntimeError("disk I/O failure during sizing")

        service = _make_service(sizer=_BrokenSizer())
        with pytest.raises(RuntimeError, match="disk I/O failure during sizing"):
            _intents(service, [_signal("AAPL")], prices={"AAPL": 150.0})

    def test_all_symbols_rejected_by_risk_raises_on_first(self) -> None:
        """When pre-trade risk rejects every order, the first rejection raises immediately."""
        service = _make_service(risk=_RejectAllRiskService())
        with pytest.raises(SafetyError):
            _intents(service, [_signal("AAPL"), _signal("MSFT")])

    def test_mixed_errors_domain_errors_skipped_safety_error_propagates(self) -> None:
        """Domain sizing errors are skipped per-symbol, but a SafetyError still propagates."""
        # NVDA → SafetyError; AMZN → missing price (skipped); AAPL → succeeds
        service = _make_service(risk=_RiskService(reject_symbol="NVDA"))
        with pytest.raises(SafetyError):
            _intents(
                service,
                [_signal("AAPL"), _signal("AMZN"), _signal("NVDA")],
                prices={"AAPL": 150.0, "NVDA": 900.0},  # AMZN price absent
            )

    def test_sell_signal_against_held_position_generates_sell_intent(self) -> None:
        """SELL signal while holding a position produces a SELL order intent."""
        service = _make_service()
        positions = {"AAPL": _position("AAPL", qty=10)}
        results = _intents(
            service,
            [_signal("AAPL", direction=SignalDirection.SELL)],
            positions=positions,
            prices={"AAPL": 155.0},
        )

        assert len(results) == 1
        assert results[0].symbol == "AAPL"
        assert results[0].side == Side.SELL

    def test_flat_signal_against_no_position_produces_no_intent(self) -> None:
        """FLAT signal with zero current position yields no delta and no intent."""
        service = _make_service()
        results = _intents(service, [_signal("AAPL", direction=SignalDirection.FLAT)])
        assert results == []

    def test_generator_can_be_materialised_twice_independently(self) -> None:
        """Calling list() twice on the same generator only yields once (standard generator semantics)."""
        service = _make_service()
        gen = service.generate_order_intents(
            signals=[_signal("AAPL")],
            positions={},
            prices={"AAPL": 150.0},
            run_id=_RUN_ID,
            strategy_id=_STRATEGY_ID,
            bar_timestamp=_BAR_TS,
            now=_NOW,
        )
        first = list(gen)
        second = list(gen)  # generator is now exhausted
        assert len(first) == 1
        assert second == []


# ---------------------------------------------------------------------------
# Bug #3 — _fetch_prices must include position symbols
# ---------------------------------------------------------------------------


class TestPriceFetchCoverage:
    def test_fetch_prices_called_with_given_symbols(self) -> None:
        """_fetch_prices passes exactly the symbols it receives to get_latest_trades."""
        from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
            _fetch_prices,
        )

        broker = MagicMock()
        broker.get_latest_trades.return_value = {
            "AAPL": {"p": 150.0},
            "HELD": {"p": 80.0},
        }

        result = _fetch_prices(broker, ["AAPL", "HELD"])

        broker.get_latest_trades.assert_called_once_with(["AAPL", "HELD"])
        assert result["AAPL"] == pytest.approx(150.0)
        assert result["HELD"] == pytest.approx(80.0)

    def test_fetch_prices_returns_empty_without_calling_broker(self) -> None:
        """_fetch_prices short-circuits and never hits the broker when symbols is empty."""
        from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
            _fetch_prices,
        )

        broker = MagicMock()
        result = _fetch_prices(broker, [])

        broker.get_latest_trades.assert_not_called()
        assert result == {}

    def test_fetch_prices_omits_symbols_with_no_broker_trade(self) -> None:
        """Symbols not present in broker response are omitted from the result (not KeyError)."""
        from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
            _fetch_prices,
        )

        broker = MagicMock()
        broker.get_latest_trades.return_value = {"AAPL": {"p": 150.0}}  # MSFT absent

        result = _fetch_prices(broker, ["AAPL", "MSFT"])

        assert "AAPL" in result
        assert "MSFT" not in result

    def test_held_position_without_signal_gets_exit_intent_when_price_provided(self) -> None:
        """Exit delta for a position-only symbol succeeds when its price is in the prices dict.

        Before Bug #3 was fixed, _fetch_prices only received signal_symbols.  A held
        position with no new signal this bar had no price in the dict, causing KeyError
        in build_order_intent and silently dropping the exit order.

        This test verifies the component contract: generate_order_intents CAN produce a
        SELL intent for HELD as long as the caller supplies prices["HELD"].  The caller
        (run_trading_evaluation_job) fulfils this by unioning signal_symbols | position_symbols.
        """
        service = _make_service()
        # HELD is in positions but has no signal — the exit delta is -50 shares
        positions = {"HELD": _position("HELD", qty=50)}
        signals: list[Signal] = []  # no new signals this bar
        prices = {"HELD": 80.0}  # available because the caller included position symbols

        results = _intents(service, signals, positions=positions, prices=prices)

        symbols = {i.symbol for i in results}
        assert "HELD" in symbols, (
            "Exit intent for HELD must be produced when the position price is in the prices dict"
        )
        assert results[0].side == Side.SELL

    def test_exit_intent_missing_when_price_absent(self) -> None:
        """If position symbol is absent from prices, the exit intent is skipped (KeyError handled).

        This documents the original Bug #3 failure mode: the exit delta exists but
        build_order_intent raises KeyError because the price is absent.  The generator
        catches KeyError per-symbol and logs a warning instead of crashing.
        """
        service = _make_service()
        positions = {"HELD": _position("HELD", qty=50)}
        signals: list[Signal] = []
        prices: dict[str, float] = {}  # HELD price absent — simulates pre-fix behaviour

        results = _intents(service, signals, positions=positions, prices=prices)

        # The generator swallows the KeyError, so no crash, but also no exit intent
        symbols = {i.symbol for i in results}
        assert "HELD" not in symbols

    def test_new_signal_symbols_included_alongside_position_symbols(self) -> None:
        """Prices include both signal symbols and position symbols simultaneously."""
        service = _make_service()
        positions = {"OLD": _position("OLD", qty=5)}
        signals = [_signal("NEW")]  # NEW is a new entry, OLD needs an exit
        prices = {"NEW": 200.0, "OLD": 50.0}  # union of both sets

        results = _intents(service, signals, positions=positions, prices=prices)

        symbols = {i.symbol for i in results}
        # NEW → BUY intent (target=10, current=0 → delta=+10)
        # OLD → SELL intent (target=0 from no signal, current=5 → delta=-5)
        assert "NEW" in symbols
        assert "OLD" in symbols
