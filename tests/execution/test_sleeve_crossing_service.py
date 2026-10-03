from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.sleeve_crossing_service import (
    SleeveCrossingService,
)
from autonomous_trading_platform.scheduler.jobs.portfolio_evaluation import (
    _cap_buys_to_budget,
    _cap_buys_to_symbol_limit,
    _clamp_sells_to_account,
)

_RUN = UUID("00000000-0000-0000-0000-00000000cccc")
_NOW = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
_PRICES = {"AAPL": 100.0, "MSFT": 400.0}


class _PassRisk:
    def assert_order_allowed(self, order_intent: object, now: object) -> None:
        return None


@pytest.fixture
def construction() -> PortfolioConstructionService:
    return PortfolioConstructionService(
        pre_trade_risk_service=_PassRisk(),  # type: ignore[arg-type]
        position_sizer=object(),  # type: ignore[arg-type]
    )


def _intent(
    construction: PortfolioConstructionService, strategy_id: str, symbol: str, delta: int
) -> OrderIntent:
    intent = construction.build_order_intent(
        delta={"symbol": symbol, "delta_qty": delta},
        prices=_PRICES,
        run_id=_RUN,
        strategy_id=strategy_id,
        bar_timestamp=_NOW,
        now=_NOW,
    )
    intent.metadata = {"sizing": strategy_id}
    return intent


def _summary(intents: list[OrderIntent]) -> set[tuple[str, str, str, int]]:
    return {(i.strategy_id, i.symbol, i.side.value, int(i.qty or 0)) for i in intents}


class TestCrossing:
    def test_opposing_orders_cross_and_only_the_residual_is_sent(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", 10), _intent(construction, "B", "AAPL", -4)]

        plan = SleeveCrossingService(construction).plan(intents, prices=_PRICES, run_id=_RUN)

        assert len(plan.crosses) == 1
        cross = plan.crosses[0]
        assert (cross.buyer_strategy_id, cross.seller_strategy_id) == ("A", "B")
        assert cross.quantity == Decimal("4")
        assert cross.price == Decimal("100.0")
        assert _summary(plan.residual_intents) == {("A", "AAPL", "buy", 6)}
        residual = plan.residual_intents[0]
        assert residual.metadata is not None
        assert residual.metadata["sizing"] == "A"
        assert residual.metadata["crossed_qty"] == "4"
        assert residual.intent_id != intents[0].intent_id

    def test_fully_offsetting_orders_send_nothing(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", 5), _intent(construction, "B", "AAPL", -5)]

        plan = SleeveCrossingService(construction).plan(intents, prices=_PRICES, run_id=_RUN)

        assert plan.residual_intents == []
        assert plan.crosses[0].quantity == Decimal("5")

    def test_one_buyer_crosses_against_several_sellers(self, construction) -> None:
        intents = [
            _intent(construction, "A", "AAPL", 10),
            _intent(construction, "B", "AAPL", -3),
            _intent(construction, "C", "AAPL", -4),
        ]

        plan = SleeveCrossingService(construction).plan(intents, prices=_PRICES, run_id=_RUN)

        assert sum(c.quantity for c in plan.crosses) == Decimal("7")
        assert _summary(plan.residual_intents) == {("A", "AAPL", "buy", 3)}

    def test_same_side_and_other_symbols_pass_through_untouched(self, construction) -> None:
        intents = [
            _intent(construction, "A", "AAPL", 10),
            _intent(construction, "B", "AAPL", 5),
            _intent(construction, "B", "MSFT", -2),
        ]

        plan = SleeveCrossingService(construction).plan(intents, prices=_PRICES, run_id=_RUN)

        assert plan.crosses == []
        assert plan.residual_intents == intents

    def test_missing_price_disables_crossing(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", 10), _intent(construction, "B", "AAPL", -4)]

        plan = SleeveCrossingService(construction).plan(intents, prices={}, run_id=_RUN)

        assert plan.crosses == []
        assert plan.residual_intents == intents

    def test_cross_ids_are_deterministic(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", 10), _intent(construction, "B", "AAPL", -4)]
        service = SleeveCrossingService(construction)

        first = service.plan(intents, prices=_PRICES, run_id=_RUN)
        second = service.plan(intents, prices=_PRICES, run_id=_RUN)

        assert first.crosses[0].cross_id == second.crosses[0].cross_id


class TestSellClamp:
    def test_sells_never_exceed_account_holdings(self, construction) -> None:
        intents = [
            _intent(construction, "A", "AAPL", -6),
            _intent(construction, "B", "AAPL", -6),
            _intent(construction, "A", "MSFT", 3),
        ]
        account = {"AAPL": Position(symbol="AAPL", quantity=Decimal("8"))}

        result, clamped = _clamp_sells_to_account(
            intents, account, construction=construction, prices=_PRICES
        )

        assert clamped == {"AAPL": Decimal("4")}
        assert _summary(result) == {
            ("A", "AAPL", "sell", 6),
            ("B", "AAPL", "sell", 2),
            ("A", "MSFT", "buy", 3),
        }

    def test_sell_of_symbol_not_held_is_dropped(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", -5)]

        result, clamped = _clamp_sells_to_account(
            intents, {}, construction=construction, prices=_PRICES
        )

        assert result == []
        assert clamped == {"AAPL": Decimal("5")}

    def test_sells_within_holdings_are_unchanged(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", -5)]
        account = {"AAPL": Position(symbol="AAPL", quantity=Decimal("5"))}

        result, clamped = _clamp_sells_to_account(
            intents, account, construction=construction, prices=_PRICES
        )

        assert result == intents
        assert clamped == {}
        assert result[0].side == Side.SELL


class TestBudgetCap:
    def test_buys_are_scaled_so_the_sleeve_fits_its_budget(self, construction) -> None:
        # Held: 50 AAPL @100 = 5000. Budget 10000 leaves 5000 of room.
        # Buys requested: 100 AAPL (10000) + 25 MSFT (10000) = 20000 -> scale 0.25.
        positions = {
            "AAPL": Position(symbol="AAPL", quantity=Decimal("50"), avg_cost=Decimal("90"))
        }
        intents = [_intent(construction, "A", "AAPL", 100), _intent(construction, "A", "MSFT", 25)]

        result, trimmed = _cap_buys_to_budget(
            intents,
            positions,
            budget_usd=Decimal("10000"),
            construction=construction,
            prices=_PRICES,
        )

        assert _summary(result) == {("A", "AAPL", "buy", 25), ("A", "MSFT", "buy", 6)}
        assert trimmed == Decimal("20000") - Decimal("2500") - Decimal("2400")
        assert result[0].metadata is not None
        assert result[0].metadata["budget_trimmed_from_qty"] == "100"

    def test_sells_free_up_room_and_are_never_reduced(self, construction) -> None:
        positions = {
            "AAPL": Position(symbol="AAPL", quantity=Decimal("100"), avg_cost=Decimal("100"))
        }
        intents = [_intent(construction, "A", "AAPL", -100), _intent(construction, "A", "MSFT", 20)]

        result, trimmed = _cap_buys_to_budget(
            intents,
            positions,
            budget_usd=Decimal("10000"),
            construction=construction,
            prices=_PRICES,
        )

        # Selling all AAPL leaves 10000 of room; 20 MSFT = 8000 fits.
        assert trimmed == Decimal("0")
        assert result == intents

    def test_full_sleeve_gets_no_new_buys(self, construction) -> None:
        positions = {
            "AAPL": Position(symbol="AAPL", quantity=Decimal("120"), avg_cost=Decimal("100"))
        }
        intents = [_intent(construction, "A", "MSFT", 5)]

        result, trimmed = _cap_buys_to_budget(
            intents,
            positions,
            budget_usd=Decimal("10000"),
            construction=construction,
            prices=_PRICES,
        )

        assert result == []
        assert trimmed == Decimal("2000")


class TestClientOrderIdLength:
    _LONG = "factor_based__19abece93f5b760d646be3cf9b16d8bbaff468d78c1ebc70a6c041cd2b56df79"

    def test_long_research_strategy_ids_fit_the_broker_order_column(self, construction) -> None:
        intent = _intent(construction, self._LONG, "MSFT", 10)

        assert len(intent.client_order_id) <= 64
        assert intent.client_order_id.startswith("factor_based__1")

    def test_long_ids_sharing_a_prefix_stay_distinct(self, construction) -> None:
        other = self._LONG[:-1] + "0"

        first = _intent(construction, self._LONG, "AAPL", 10)
        second = _intent(construction, other, "AAPL", 10)

        assert first.client_order_id != second.client_order_id

    def test_short_ids_keep_their_historical_format(self, construction) -> None:
        intent = _intent(construction, "momentum_v1", "AAPL", 10)

        assert intent.client_order_id.startswith("momentum_v1-AAPL-")


class TestOrderLimitRejection:
    class _Sizer:
        def compute_quantity(self, **kwargs):
            from autonomous_trading_platform.execution.services.position_sizer import SizingResult

            return SizingResult(
                quantity=10,
                base_notional=Decimal("1000"),
                final_notional=Decimal("1000"),
                combined_scalar=None,
                scaling_applied=False,
            )

    class _RejectSymbol:
        def __init__(self, symbol: str, error: type[Exception]) -> None:
            self.symbol, self.error = symbol, error

        def assert_order_allowed(self, order_intent, now) -> None:
            if order_intent.symbol == self.symbol:
                raise self.error("limit breached")

    def _signals(self):
        from autonomous_trading_platform.contracts.common.enums import SignalDirection
        from autonomous_trading_platform.contracts.trading.signal import Signal

        return [
            Signal(
                signal_id=UUID(int=i + 1),
                run_id=_RUN,
                timestamp=_NOW,
                bar_timestamp=_NOW,
                strategy_id="A",
                symbol=symbol,
                direction=SignalDirection.BUY,
                confidence=1.0,
            )
            for i, symbol in enumerate(("AAPL", "MSFT"))
        ]

    def _generate(self, risk, *, skip: bool):
        service = PortfolioConstructionService(
            pre_trade_risk_service=risk,  # type: ignore[arg-type]
            position_sizer=self._Sizer(),  # type: ignore[arg-type]
        )
        return list(
            service.generate_order_intents(
                signals=self._signals(),
                positions={},
                prices=_PRICES,
                run_id=_RUN,
                strategy_id="A",
                bar_timestamp=_NOW,
                now=_NOW,
                skip_order_limit_breaches=skip,
            )
        )

    def test_order_limit_breach_drops_only_that_order_in_portfolio_mode(self) -> None:
        from autonomous_trading_platform.safety.errors import SymbolExposureLimitExceededError

        intents = self._generate(
            self._RejectSymbol("AAPL", SymbolExposureLimitExceededError), skip=True
        )

        assert [i.symbol for i in intents] == ["MSFT"]

    def test_order_limit_breach_still_raises_in_legacy_mode(self) -> None:
        from autonomous_trading_platform.safety.errors import SymbolExposureLimitExceededError

        with pytest.raises(SymbolExposureLimitExceededError):
            self._generate(self._RejectSymbol("AAPL", SymbolExposureLimitExceededError), skip=False)

    def test_global_safety_errors_always_raise(self) -> None:
        from autonomous_trading_platform.safety.errors import KillSwitchEnabledError

        with pytest.raises(KillSwitchEnabledError):
            self._generate(self._RejectSymbol("AAPL", KillSwitchEnabledError), skip=True)


class TestSymbolCapAcrossSleeves:
    """Portfolio rotation step 5 finding: two sleeves each bought 125 JPM in one cycle,
    both checked against start-of-cycle holdings, and the account ended far above
    the per-symbol cap."""

    def test_second_sleeve_is_trimmed_to_the_remaining_room(self, construction) -> None:
        # Cap 25000; account holds 50 AAPL (5000). A wants 125 (12500), B wants 125.
        account = {"AAPL": Position(symbol="AAPL", quantity=Decimal("50"), avg_cost=Decimal("100"))}
        intents = [_intent(construction, "A", "AAPL", 125), _intent(construction, "B", "AAPL", 125)]

        result, trimmed = _cap_buys_to_symbol_limit(
            intents, account, cap_usd=Decimal("25000"), construction=construction, prices=_PRICES
        )

        assert _summary(result) == {("A", "AAPL", "buy", 125), ("B", "AAPL", "buy", 75)}
        assert trimmed == {"AAPL": Decimal("50")}
        rebuilt = next(i for i in result if i.strategy_id == "B")
        assert rebuilt.metadata is not None and rebuilt.metadata["symbol_cap_trimmed_from"] == "125"

    def test_sells_in_the_same_cycle_free_room(self, construction) -> None:
        account = {
            "AAPL": Position(symbol="AAPL", quantity=Decimal("250"), avg_cost=Decimal("100"))
        }
        intents = [
            _intent(construction, "A", "AAPL", -125),
            _intent(construction, "B", "AAPL", 100),
        ]

        result, trimmed = _cap_buys_to_symbol_limit(
            intents, account, cap_usd=Decimal("25000"), construction=construction, prices=_PRICES
        )

        # 250 - 125 sold = 125 (12500) + 100 bought (10000) = 22500 <= 25000.
        assert trimmed == {}
        assert result == intents

    def test_symbol_over_the_cap_gets_no_new_buys_but_sells_pass(self, construction) -> None:
        account = {
            "AAPL": Position(symbol="AAPL", quantity=Decimal("300"), avg_cost=Decimal("100"))
        }
        intents = [_intent(construction, "A", "AAPL", -50), _intent(construction, "B", "AAPL", 10)]

        result, trimmed = _cap_buys_to_symbol_limit(
            intents, account, cap_usd=Decimal("25000"), construction=construction, prices=_PRICES
        )

        assert _summary(result) == {("A", "AAPL", "sell", 50)}
        assert trimmed == {"AAPL": Decimal("10")}

    def test_no_cap_leaves_intents_alone(self, construction) -> None:
        intents = [_intent(construction, "A", "AAPL", 500)]

        result, trimmed = _cap_buys_to_symbol_limit(
            intents, {}, cap_usd=None, construction=construction, prices=_PRICES
        )

        assert result == intents and trimmed == {}
