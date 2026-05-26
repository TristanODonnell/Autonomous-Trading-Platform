"""
Tests for FINDING-02: Cross-Strategy Portfolio Symbol Exposure Limits.

Covers:
- PortfolioRiskStateReader aggregation and computation
- PreTradeRiskService portfolio-level symbol exposure checks
- Multi-strategy symbol collision detection
- Projected exposure rejection before order placement
- SELL orders reducing exposure are allowed
- Audit event emission on rejection
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    OrderSource,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import PortfolioSymbolExposureLimitExceededError
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order_intent(
    *,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    qty: float = 10.0,
    limit_price: float = 100.0,
    strategy_id: str = "strat-1",
) -> OrderIntent:
    now = datetime.now(UTC)
    return OrderIntent(
        intent_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        run_id=uuid.uuid4(),
        strategy_id=strategy_id,
        timestamp=now,
        bar_timestamp=now,
        symbol=symbol,
        side=side,
        qty=Decimal(str(qty)),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(str(limit_price)),
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        client_order_id=str(uuid.uuid4()),
    )


def _make_settings(**overrides):
    settings = MagicMock()
    settings.max_gross_exposure = 1_000_000.0
    settings.max_symbol_exposure = 100_000.0
    settings.max_daily_notional_traded = 500_000.0
    settings.max_reserved_cash = 1_000_000.0
    for key, val in overrides.items():
        setattr(settings, key, val)
    return settings


def _make_service(
    *,
    portfolio_reader: PortfolioRiskStateReader | None = None,
    max_portfolio_symbol_exposure_usd: float | None = None,
    max_portfolio_symbol_pct: float | None = None,
    audit_log_repo=None,
) -> PreTradeRiskService:
    return PreTradeRiskService(
        settings=_make_settings(),
        risk_state_reader=StubRiskStateReader(),
        portfolio_risk_state_reader=portfolio_reader,
        max_portfolio_symbol_exposure_usd=max_portfolio_symbol_exposure_usd,
        max_portfolio_symbol_pct=max_portfolio_symbol_pct,
        audit_log_repo=audit_log_repo,
    )


# ---------------------------------------------------------------------------
# PortfolioRiskStateReader unit tests
# ---------------------------------------------------------------------------


class TestPortfolioRiskStateReader:
    def test_get_symbol_exposure_usd_known_symbol(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("5000"), "MSFT": Decimal("3000")},
            total_equity=Decimal("50000"),
        )
        assert reader.get_symbol_exposure_usd("AAPL") == Decimal("5000")

    def test_get_symbol_exposure_usd_unknown_symbol_returns_zero(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("5000")},
            total_equity=Decimal("50000"),
        )
        assert reader.get_symbol_exposure_usd("TSLA") == Decimal("0")

    def test_get_symbol_exposure_pct(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("10000")},
            total_equity=Decimal("100000"),
        )
        assert reader.get_symbol_exposure_pct("AAPL") == pytest.approx(Decimal("0.10"))

    def test_get_symbol_exposure_pct_zero_equity(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("10000")},
            total_equity=Decimal("0"),
        )
        assert reader.get_symbol_exposure_pct("AAPL") == Decimal("0")

    def test_get_all_symbol_exposures_returns_copy(self) -> None:
        data = {"AAPL": Decimal("5000"), "MSFT": Decimal("3000")}
        reader = PortfolioRiskStateReader(symbol_exposures_usd=data, total_equity=Decimal("50000"))
        result = reader.get_all_symbol_exposures()
        result["AAPL"] = Decimal("9999")
        # Original state must be unchanged
        assert reader.get_symbol_exposure_usd("AAPL") == Decimal("5000")

    def test_get_top_concentrated_symbols_ordering(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={
                "AAPL": Decimal("10000"),
                "TSLA": Decimal("30000"),
                "MSFT": Decimal("20000"),
            },
            total_equity=Decimal("200000"),
        )
        top = reader.get_top_concentrated_symbols(2)
        assert top[0][0] == "TSLA"
        assert top[1][0] == "MSFT"
        assert len(top) == 2

    def test_get_top_concentrated_symbols_all_when_n_exceeds_count(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("5000")},
            total_equity=Decimal("100000"),
        )
        assert len(reader.get_top_concentrated_symbols(10)) == 1

    def test_total_equity_property(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={},
            total_equity=Decimal("75000"),
        )
        assert reader.total_equity == Decimal("75000")

    def test_multi_strategy_aggregation(self) -> None:
        # Simulate three strategies each holding AAPL
        momentum_aapl = Decimal("5000")
        macd_aapl = Decimal("4000")
        mean_rev_aapl = Decimal("3000")
        aggregate = {"AAPL": momentum_aapl + macd_aapl + mean_rev_aapl}

        reader = PortfolioRiskStateReader(
            symbol_exposures_usd=aggregate,
            total_equity=Decimal("100000"),
        )
        assert reader.get_symbol_exposure_usd("AAPL") == Decimal("12000")
        assert reader.get_symbol_exposure_pct("AAPL") == pytest.approx(Decimal("0.12"))

    def test_aggregate_calculations_are_deterministic(self) -> None:
        data = {f"SYM{i}": Decimal(str(i * 1000)) for i in range(1, 6)}
        equity = Decimal("100000")
        reader1 = PortfolioRiskStateReader(symbol_exposures_usd=data, total_equity=equity)
        reader2 = PortfolioRiskStateReader(symbol_exposures_usd=data, total_equity=equity)
        for sym in data:
            assert reader1.get_symbol_exposure_usd(sym) == reader2.get_symbol_exposure_usd(sym)
            assert reader1.get_symbol_exposure_pct(sym) == reader2.get_symbol_exposure_pct(sym)

    def test_from_session_uses_latest_portfolio_snapshot(self, db_session: Session) -> None:
        older_snapshot_id = uuid.uuid4()
        latest_snapshot_id = uuid.uuid4()
        older_time = datetime(2026, 1, 1, tzinfo=UTC)
        latest_time = datetime(2026, 1, 2, tzinfo=UTC)
        run_id = uuid.uuid4()

        db_session.add_all(
            [
                PositionSnapshot(
                    snapshot_id=older_snapshot_id,
                    run_id=run_id,
                    timestamp=older_time,
                    source=OrderSource.LEDGER,
                ),
                PositionSnapshotItem(
                    snapshot_id=older_snapshot_id,
                    symbol="AAPL",
                    quantity=Decimal("999"),
                    avg_cost=Decimal("100"),
                    market_price=Decimal("100"),
                    market_value=Decimal("99900"),
                    unrealized_pnl=Decimal("0"),
                ),
                PositionSnapshot(
                    snapshot_id=latest_snapshot_id,
                    run_id=run_id,
                    timestamp=latest_time,
                    source=OrderSource.LEDGER,
                ),
                PositionSnapshotItem(
                    snapshot_id=latest_snapshot_id,
                    symbol="AAPL",
                    quantity=Decimal("50"),
                    avg_cost=Decimal("100"),
                    market_price=Decimal("100"),
                    market_value=Decimal("5000"),
                    unrealized_pnl=Decimal("0"),
                ),
                PositionSnapshotItem(
                    snapshot_id=latest_snapshot_id,
                    symbol="MSFT",
                    quantity=Decimal("20"),
                    avg_cost=Decimal("200"),
                    market_price=Decimal("250"),
                    market_value=Decimal("5000"),
                    unrealized_pnl=Decimal("1000"),
                ),
                CashSnapshot(
                    snapshot_id=uuid.uuid4(),
                    run_id=run_id,
                    timestamp=latest_time,
                    currency="USD",
                    cash=Decimal("90000"),
                    buying_power=Decimal("90000"),
                    reserved_cash=Decimal("0"),
                    equity=Decimal("100000"),
                    source=OrderSource.LEDGER,
                    capital_bucket=None,
                    settled_cash=Decimal("90000"),
                    unsettled_cash=Decimal("0"),
                ),
            ]
        )
        db_session.flush()

        reader = PortfolioRiskStateReader.from_session(db_session)

        assert reader.get_symbol_exposure_usd("AAPL") == Decimal("5000")
        assert reader.get_symbol_exposure_usd("MSFT") == Decimal("5000")
        assert reader.get_symbol_exposure_pct("AAPL") == Decimal("0.05")
        assert reader.get_symbol_quantity("AAPL") == Decimal("50")


# ---------------------------------------------------------------------------
# PreTradeRiskService — no portfolio reader (backward compat)
# ---------------------------------------------------------------------------


class TestPreTradeRiskServiceNoPortfolioReader:
    def test_passes_when_no_portfolio_reader_configured(self) -> None:
        svc = _make_service()
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        # Must not raise — portfolio checks are skipped when no reader
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_passes_when_reader_set_but_no_limits(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("999999")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(portfolio_reader=reader)
        order = _make_order_intent()
        # No limits configured → no check → must pass
        svc.assert_order_allowed(order, datetime.now(UTC))


# ---------------------------------------------------------------------------
# Portfolio USD limit checks
# ---------------------------------------------------------------------------


class TestPortfolioUSDLimit:
    def test_below_limit_passes(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("5000")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=10000.0,  # 5000 + 1000 (10 * 100) = 6000 < 10000
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)  # notional = 1000
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_exactly_at_limit_passes(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("9000")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=10000.0,  # 9000 + 1000 = 10000 exactly
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_above_limit_raises(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("9500")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=10000.0,  # 9500 + 1000 = 10500 > 10000
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        with pytest.raises(PortfolioSymbolExposureLimitExceededError) as exc_info:
            svc.assert_order_allowed(order, datetime.now(UTC))

        err = exc_info.value
        assert err.symbol == "AAPL"
        assert err.strategy_id == "strat-1"
        assert err.projected_exposure_usd == pytest.approx(10500.0)
        assert err.limit_usd == pytest.approx(10000.0)

    def test_multi_strategy_aggregate_triggers_rejection(self) -> None:
        # Three strategies each at 8000 USD → total = 24000; limit = 25000
        # Proposed order adds 2000 → projected = 26000 > 25000
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("24000")},
            total_equity=Decimal("200000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=25000.0,
        )
        order = _make_order_intent(qty=20.0, limit_price=100.0)  # notional = 2000
        with pytest.raises(PortfolioSymbolExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))

    def test_different_symbol_not_checked_against_aapl_limit(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("9500")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=10000.0,
        )
        # MSFT order should not be blocked by AAPL's portfolio limit
        order = _make_order_intent(symbol="MSFT", qty=10.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))


# ---------------------------------------------------------------------------
# Portfolio pct limit checks
# ---------------------------------------------------------------------------


class TestPortfolioPctLimit:
    def test_below_pct_limit_passes(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("15000")},
            total_equity=Decimal("100000"),
        )
        # 15000 + 1000 = 16000 → 16% < 20%
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_exactly_at_pct_limit_passes(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("19000")},
            total_equity=Decimal("100000"),
        )
        # 19000 + 1000 = 20000 → 20% == 20%
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_above_pct_limit_raises(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("18000")},
            total_equity=Decimal("100000"),
        )
        # 18000 + 5000 = 23000 → 23% > 20%
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=50.0, limit_price=100.0)
        with pytest.raises(PortfolioSymbolExposureLimitExceededError) as exc_info:
            svc.assert_order_allowed(order, datetime.now(UTC))

        err = exc_info.value
        assert err.projected_exposure_pct == pytest.approx(0.23)
        assert err.limit_pct == pytest.approx(0.20)

    def test_example_from_spec_rejected(self) -> None:
        # From tasks.txt: current 18%, limit 20%, proposed brings to 24% → rejected
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("18000")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=60.0, limit_price=100.0)  # +6000 → 24%
        with pytest.raises(PortfolioSymbolExposureLimitExceededError) as exc_info:
            svc.assert_order_allowed(order, datetime.now(UTC))

        err = exc_info.value
        assert err.projected_exposure_pct == pytest.approx(0.24)


# ---------------------------------------------------------------------------
# Both USD and pct limits together
# ---------------------------------------------------------------------------


class TestBothLimits:
    def test_usd_breach_blocks_even_if_pct_ok(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("49500")},
            total_equity=Decimal("1_000_000"),
        )
        # pct: (49500 + 1000) / 1M = 5.05% << 20% limit → pct OK
        # usd: 49500 + 1000 = 50500 > 50000 → USD breach
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=50000.0,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        with pytest.raises(PortfolioSymbolExposureLimitExceededError) as exc_info:
            svc.assert_order_allowed(order, datetime.now(UTC))
        assert exc_info.value.limit_usd == pytest.approx(50000.0)

    def test_pct_breach_blocks_even_if_usd_ok(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("19000")},
            total_equity=Decimal("100000"),
        )
        # pct: 19000 + 5000 = 24000 → 24% > 20% → pct breach
        # usd: 24000 < 100000 → USD OK
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=100_000.0,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(qty=50.0, limit_price=100.0)
        with pytest.raises(PortfolioSymbolExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))


# ---------------------------------------------------------------------------
# SELL orders (exposure reduction)
# ---------------------------------------------------------------------------


class TestSellOrders:
    def test_sell_reducing_exposure_passes_when_projected_within_limit(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("25000")},
            total_equity=Decimal("100000"),
        )
        # SELL 10 shares at 100 reduces exposure from 25000 → 24000
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(side=Side.SELL, qty=10.0, limit_price=100.0)
        # Even though current exposure 25% > limit 20%, reducing it further is fine
        # (the check is against projected, not current)
        svc.assert_order_allowed(order, datetime.now(UTC))

    def test_sell_order_always_allowed_when_currently_over_limit(self) -> None:
        """Sell orders that reduce concentration must never be blocked."""
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("30000")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
        )
        # SELL reduces from 30% toward 0% — must not be blocked
        order = _make_order_intent(side=Side.SELL, qty=100.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    def test_blocked_order_emits_audit_event(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("19500")},
            total_equity=Decimal("100000"),
        )
        audit_repo = MagicMock()
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
            audit_log_repo=audit_repo,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)  # +1000 → 20500 → 20.5% > 20%
        with pytest.raises(PortfolioSymbolExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))

        audit_repo.record_operator_action.assert_called_once()
        call_kwargs = audit_repo.record_operator_action.call_args.kwargs
        assert call_kwargs["action"] == "PORTFOLIO_SYMBOL_EXPOSURE_BLOCKED"
        metadata = call_kwargs["metadata"]
        assert metadata["symbol"] == "AAPL"
        assert metadata["strategy_id"] == "strat-1"
        assert "projected_symbol_exposure_pct" in metadata
        assert "configured_limit_pct" in metadata

    def test_allowed_order_does_not_emit_audit_event(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("10000")},
            total_equity=Decimal("100000"),
        )
        audit_repo = MagicMock()
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
            audit_log_repo=audit_repo,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        svc.assert_order_allowed(order, datetime.now(UTC))
        audit_repo.record_operator_action.assert_not_called()

    def test_no_audit_repo_does_not_crash_on_rejection(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("19500")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_pct=0.20,
            audit_log_repo=None,
        )
        order = _make_order_intent(qty=10.0, limit_price=100.0)
        with pytest.raises(PortfolioSymbolExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))


# ---------------------------------------------------------------------------
# Error payload completeness
# ---------------------------------------------------------------------------


class TestExceptionPayload:
    def test_error_carries_all_required_fields(self) -> None:
        reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("18000")},
            total_equity=Decimal("100000"),
        )
        svc = _make_service(
            portfolio_reader=reader,
            max_portfolio_symbol_exposure_usd=20000.0,
            max_portfolio_symbol_pct=0.20,
        )
        order = _make_order_intent(
            symbol="AAPL",
            qty=50.0,
            limit_price=100.0,
            strategy_id="momentum-v2",
        )
        with pytest.raises(PortfolioSymbolExposureLimitExceededError) as exc_info:
            svc.assert_order_allowed(order, datetime.now(UTC))

        err = exc_info.value
        assert err.symbol == "AAPL"
        assert err.strategy_id == "momentum-v2"
        assert err.current_exposure_usd == pytest.approx(18000.0)
        assert err.projected_exposure_usd == pytest.approx(23000.0)
        assert err.limit_usd == pytest.approx(20000.0)
        assert err.current_exposure_pct == pytest.approx(0.18)
        assert err.projected_exposure_pct == pytest.approx(0.23)
        assert err.limit_pct == pytest.approx(0.20)
        assert "AAPL" in str(err)
        assert "momentum-v2" in str(err)


# ---------------------------------------------------------------------------
# Per-strategy checks still enforced (non-regression)
# ---------------------------------------------------------------------------


class TestPerStrategyChecksNotBroken:
    def test_per_strategy_gross_exposure_still_enforced(self) -> None:
        from autonomous_trading_platform.safety.errors import GrossExposureLimitExceededError

        settings = _make_settings(max_gross_exposure=500.0)  # tiny limit
        svc = PreTradeRiskService(settings=settings, risk_state_reader=StubRiskStateReader())
        order = _make_order_intent(qty=10.0, limit_price=100.0)  # notional=1000 > 500
        with pytest.raises(GrossExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))

    def test_per_strategy_symbol_exposure_still_enforced(self) -> None:
        from autonomous_trading_platform.safety.errors import SymbolExposureLimitExceededError

        settings = _make_settings(max_gross_exposure=100_000.0, max_symbol_exposure=500.0)
        svc = PreTradeRiskService(settings=settings, risk_state_reader=StubRiskStateReader())
        order = _make_order_intent(qty=10.0, limit_price=100.0)  # notional=1000 > 500
        with pytest.raises(SymbolExposureLimitExceededError):
            svc.assert_order_allowed(order, datetime.now(UTC))
