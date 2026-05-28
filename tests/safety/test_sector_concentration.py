"""
Tests for FINDING-10: Sector Concentration Limits and Risk Snapshot Exposure Breakdown.

Covers:
- SectorExposureReader aggregation and sector lookup
- PreTradeRiskService sector concentration enforcement
- SELL orders reducing exposure are allowed
- Unknown sector policies (reject / use_unknown_bucket / warn_allow)
- Audit event emitted on sector block
- Missing sector metadata logging/metrics
- Risk snapshot includes sector breakdown
- Existing gross/net/symbol checks still run unchanged
- RiskLimitConfig sector fields
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.safety.errors import (
    GrossExposureLimitExceededError,
    SectorConcentrationLimitExceededError,
    SymbolExposureLimitExceededError,
)
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.safety.readers.sector_exposure_reader import (
    UNKNOWN_SECTOR,
    SectorExposureReader,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)

_SECTOR_MAP = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "JPM": "Financials",
    "GS": "Financials",
    "XOM": "Energy",
}


def _make_intent(
    *,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    qty: float = 10.0,
    limit_price: float = 100.0,
    strategy_id: str = "strat-1",
) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        run_id=uuid.uuid4(),
        strategy_id=strategy_id,
        timestamp=NOW,
        bar_timestamp=NOW,
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
    s = MagicMock()
    s.max_gross_exposure = 1_000_000.0
    s.max_symbol_exposure = 500_000.0
    s.max_daily_notional_traded = 500_000.0
    s.max_reserved_cash = 1_000_000.0
    s.max_portfolio_symbol_exposure_usd = None
    s.max_portfolio_symbol_pct = None
    s.max_sector_exposure_pct = None
    s.default_max_sector_exposure_pct = None
    s.unknown_sector_policy = "reject"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_sector_reader(
    *,
    symbol_exposures: dict[str, float] | None = None,
    sector_map: dict[str, str] | None = None,
    total_equity: float = 1_000_000.0,
) -> SectorExposureReader:
    exposures = {k: Decimal(str(v)) for k, v in (symbol_exposures or {}).items()}
    return SectorExposureReader(
        symbol_exposures_usd=exposures,
        symbol_sector_map=sector_map if sector_map is not None else _SECTOR_MAP,
        total_equity=Decimal(str(total_equity)),
    )


def _make_service(
    *,
    sector_reader: SectorExposureReader | None = None,
    max_sector_exposure_pct: dict[str, float] | None = None,
    default_max_sector_exposure_pct: float | None = None,
    unknown_sector_policy: str = "reject",
    portfolio_reader: PortfolioRiskStateReader | None = None,
    audit_log_repo=None,
) -> PreTradeRiskService:
    return PreTradeRiskService(
        settings=_make_settings(),
        risk_state_reader=StubRiskStateReader(),
        portfolio_risk_state_reader=portfolio_reader,
        sector_exposure_reader=sector_reader,
        max_sector_exposure_pct=max_sector_exposure_pct,
        default_max_sector_exposure_pct=default_max_sector_exposure_pct,
        unknown_sector_policy=unknown_sector_policy,
        audit_log_repo=audit_log_repo,
    )


# ===========================================================================
# SectorExposureReader unit tests
# ===========================================================================


class TestSectorExposureReader:
    def test_aggregates_by_sector(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 100_000, "MSFT": 50_000, "JPM": 80_000},
        )
        assert reader.get_sector_exposure_usd("Technology") == Decimal("150000")
        assert reader.get_sector_exposure_usd("Financials") == Decimal("80000")
        assert reader.get_sector_exposure_usd("Energy") == ZERO

    def test_sector_exposure_pct(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 300_000},
            total_equity=1_000_000,
        )
        assert reader.get_sector_exposure_pct("Technology") == Decimal("0.3")

    def test_zero_equity_returns_zero_pct(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 100_000},
            total_equity=0,
        )
        assert reader.get_sector_exposure_pct("Technology") == ZERO

    def test_unknown_sector_bucket(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 50_000, "UNKNOWNSYM": 30_000},
            sector_map={"AAPL": "Technology"},
        )
        assert reader.get_sector_exposure_usd(UNKNOWN_SECTOR) == Decimal("30000")
        assert "UNKNOWNSYM" in reader.get_unmapped_symbols()

    def test_get_symbol_sector_returns_none_for_unmapped(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 50_000},
            sector_map={"AAPL": "Technology"},
        )
        assert reader.get_symbol_sector("AAPL") == "Technology"
        assert reader.get_symbol_sector("NVDA") is None

    def test_get_symbol_exposure_usd(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 75_000, "MSFT": 25_000},
        )
        assert reader.get_symbol_exposure_usd("AAPL") == Decimal("75000")
        assert reader.get_symbol_exposure_usd("MSFT") == Decimal("25000")
        assert reader.get_symbol_exposure_usd("NVDA") == ZERO

    def test_get_top_concentrated_sectors(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 400_000, "JPM": 200_000, "XOM": 100_000},
        )
        top = reader.get_top_concentrated_sectors(n=2)
        assert top[0] == ("Technology", Decimal("400000"))
        assert top[1] == ("Financials", Decimal("200000"))

    def test_from_portfolio_reader(self):
        portfolio_reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("200000"), "JPM": Decimal("100000")},
            total_equity=Decimal("500000"),
        )
        sector_reader = SectorExposureReader.from_portfolio_reader(portfolio_reader, _SECTOR_MAP)
        assert sector_reader.get_sector_exposure_usd("Technology") == Decimal("200000")
        assert sector_reader.get_sector_exposure_usd("Financials") == Decimal("100000")
        assert sector_reader.total_equity == Decimal("500000")

    def test_case_insensitive_symbol_lookup(self):
        reader = _make_sector_reader(
            symbol_exposures={"aapl": 100_000},
            sector_map={"AAPL": "Technology"},
        )
        assert reader.get_symbol_sector("aapl") == "Technology"
        assert reader.get_symbol_sector("AAPL") == "Technology"

    def test_concentration_metrics_structure(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 300_000, "JPM": 100_000},
            total_equity=1_000_000,
        )
        metrics = reader.get_sector_concentration_metrics(
            sector_limits={"Technology": 0.40, "Financials": 0.25},
        )
        tech = metrics["sector_exposures"]["Technology"]
        assert tech["exposure_usd"] == pytest.approx(300_000.0)
        assert tech["exposure_pct"] == pytest.approx(0.30)
        assert tech["limit_pct"] == pytest.approx(0.40)
        assert tech["utilization_pct"] == pytest.approx(0.75)

    def test_concentration_metrics_over_limit_flagged(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 450_000},
            total_equity=1_000_000,
        )
        result = reader.get_sector_concentration_metrics(
            sector_limits={"Technology": 0.40},
        )
        assert "Technology" in result["over_limit_sectors"]
        assert "Technology" not in result["near_limit_sectors"]

    def test_concentration_metrics_near_limit_flagged(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 380_000},
            total_equity=1_000_000,
        )
        result = reader.get_sector_concentration_metrics(
            sector_limits={"Technology": 0.40},
        )
        assert "Technology" in result["near_limit_sectors"]

    def test_empty_portfolio(self):
        reader = _make_sector_reader(symbol_exposures={}, total_equity=500_000)
        assert reader.get_sector_exposure_usd("Technology") == ZERO
        assert reader.get_unmapped_symbols() == set()


ZERO = Decimal("0")


# ===========================================================================
# PreTradeRiskService — sector concentration enforcement
# ===========================================================================


class TestSectorConcentrationEnforcement:
    def test_no_sector_reader_skips_check(self):
        svc = _make_service(
            sector_reader=None,
            max_sector_exposure_pct={"Technology": 0.30},
        )
        # Should not raise
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=100), NOW)

    def test_no_limits_configured_skips_check(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 400_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct=None,
            default_max_sector_exposure_pct=None,
        )
        # Should not raise — no limit to enforce
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=100), NOW)

    def test_projected_below_limit_allows_order(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 200_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        # Adding 10 shares @ $100 = $1,000 → projected 20.1% < 40% limit
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=10, limit_price=100), NOW)

    def test_projected_exactly_at_limit_allows_order(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 390_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        # Adding 10 shares @ $1,000 = $10,000 → projected = 400,000 / 1,000,000 = 40% == limit
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=10, limit_price=1_000), NOW)

    def test_projected_above_limit_blocks_order(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 390_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        # Adding 100 shares @ $1,000 = $100,000 → projected = 490,000 / 1,000,000 = 49% > 40%
        with pytest.raises(SectorConcentrationLimitExceededError) as exc_info:
            svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=1_000), NOW)
        err = exc_info.value
        assert err.symbol == "AAPL"
        assert err.sector == "Technology"
        assert err.configured_limit_pct == pytest.approx(0.40)
        assert err.projected_sector_exposure_pct > 0.40

    def test_error_carries_full_context(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 350_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        with pytest.raises(SectorConcentrationLimitExceededError) as exc_info:
            svc.assert_order_allowed(
                _make_intent(symbol="AAPL", qty=100, limit_price=1_000, strategy_id="my-strat"),
                NOW,
            )
        err = exc_info.value
        assert err.strategy_id == "my-strat"
        assert err.proposed_order_notional == pytest.approx(100_000.0)
        assert err.run_id is not None

    def test_sell_reducing_sector_exposure_is_allowed(self):
        portfolio_reader = PortfolioRiskStateReader(
            symbol_exposures_usd={"AAPL": Decimal("400000")},
            total_equity=Decimal("1000000"),
            symbol_quantities={"AAPL": Decimal("4000")},
        )
        reader = SectorExposureReader.from_portfolio_reader(portfolio_reader, _SECTOR_MAP)
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.30},
            portfolio_reader=portfolio_reader,
        )
        # Selling 10 shares @ $100 = $1,000 reduction → 399,000 / 1,000,000 = 39.9%
        # This still exceeds the 30% limit, but since it REDUCES exposure it should be allowed
        svc.assert_order_allowed(
            _make_intent(symbol="AAPL", side=Side.SELL, qty=10, limit_price=100), NOW
        )

    def test_default_sector_limit_applies_when_no_specific_limit(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 350_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={},  # no per-sector limits
            default_max_sector_exposure_pct=0.40,
        )
        # Adding $100,000 → projected 45% > default 40%
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=1_000), NOW)

    def test_specific_limit_takes_precedence_over_default(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 350_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.50},
            default_max_sector_exposure_pct=0.20,  # more restrictive default
        )
        # Adding $100,000 → projected 45%, within sector-specific 50% limit
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=1_000), NOW)

    def test_multi_symbol_same_sector_aggregates(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 200_000, "MSFT": 150_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        # Tech = 350k = 35%. Adding 100 NVDA @ $1,000 = $100k → 45% > 40%
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="NVDA", qty=100, limit_price=1_000), NOW)

    def test_no_limit_for_sector_allows_any_exposure(self):
        reader = _make_sector_reader(
            symbol_exposures={"JPM": 900_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.30},  # only Tech limited
            default_max_sector_exposure_pct=None,
        )
        # Financials has no limit — buying more JPM should not be blocked
        svc.assert_order_allowed(_make_intent(symbol="JPM", qty=10, limit_price=100), NOW)


# ===========================================================================
# Unknown sector policy
# ===========================================================================


class TestUnknownSectorPolicy:
    def test_reject_policy_blocks_unmapped_symbol(self):
        reader = _make_sector_reader(
            symbol_exposures={},
            sector_map={"AAPL": "Technology"},  # TSLA not in map
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
            default_max_sector_exposure_pct=0.30,
            unknown_sector_policy="reject",
        )
        with pytest.raises(SectorConcentrationLimitExceededError) as exc_info:
            svc.assert_order_allowed(_make_intent(symbol="TSLA", qty=10, limit_price=100), NOW)
        err = exc_info.value
        assert err.sector == UNKNOWN_SECTOR
        assert err.symbol == "TSLA"

    def test_warn_allow_policy_allows_unmapped_symbol(self):
        reader = _make_sector_reader(
            symbol_exposures={},
            sector_map={"AAPL": "Technology"},
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
            unknown_sector_policy="warn_allow",
        )
        # TSLA unmapped but warn_allow → should not raise
        svc.assert_order_allowed(_make_intent(symbol="TSLA", qty=10, limit_price=100), NOW)

    def test_use_unknown_bucket_policy_allows_when_under_default(self):
        reader = _make_sector_reader(
            symbol_exposures={"TSLA": 100_000},
            sector_map={"AAPL": "Technology"},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={},
            default_max_sector_exposure_pct=0.50,
            unknown_sector_policy="use_unknown_bucket",
        )
        # UNKNOWN bucket = 100k = 10%, adding $50k → 15% < 50% default
        svc.assert_order_allowed(_make_intent(symbol="TSLA", qty=500, limit_price=100), NOW)

    def test_use_unknown_bucket_policy_blocks_when_over_default(self):
        reader = _make_sector_reader(
            symbol_exposures={"TSLA": 450_000},
            sector_map={"AAPL": "Technology"},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={},
            default_max_sector_exposure_pct=0.50,
            unknown_sector_policy="use_unknown_bucket",
        )
        # UNKNOWN = 450k = 45%, adding 100k → 55% > 50%
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="TSLA", qty=1000, limit_price=100), NOW)

    def test_use_unknown_bucket_with_explicit_unknown_limit(self):
        reader = _make_sector_reader(
            symbol_exposures={"TSLA": 300_000},
            sector_map={"AAPL": "Technology"},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"UNKNOWN": 0.25},
            default_max_sector_exposure_pct=0.50,
            unknown_sector_policy="use_unknown_bucket",
        )
        # UNKNOWN = 30%, UNKNOWN limit = 25% → adding anything should block (already over)
        # But since it's at 30% > 25% already, any BUY increases exposure → blocked
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="TSLA", qty=10, limit_price=100), NOW)

    def test_invalid_unknown_sector_policy_raises_on_init(self):
        with pytest.raises(ValueError, match="unknown_sector_policy"):
            PreTradeRiskService(
                settings=_make_settings(),
                risk_state_reader=StubRiskStateReader(),
                unknown_sector_policy="invalid_policy",
            )


# ===========================================================================
# Audit and observability
# ===========================================================================


class TestSectorAuditAndObservability:
    def test_audit_event_emitted_on_block(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 390_000},
            total_equity=1_000_000,
        )
        audit_repo = MagicMock()
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
            audit_log_repo=audit_repo,
        )
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=1_000), NOW)

        audit_repo.record_operator_action.assert_called_once()
        call_kwargs = audit_repo.record_operator_action.call_args.kwargs
        assert call_kwargs["action"] == "SECTOR_CONCENTRATION_LIMIT_BLOCKED"
        assert call_kwargs["component"] == "pre_trade_risk"
        metadata = call_kwargs["metadata"]
        assert metadata["sector"] == "Technology"
        assert metadata["symbol"] == "AAPL"
        assert "projected_sector_exposure_pct" in metadata
        assert "configured_limit_pct" in metadata

    def test_no_audit_event_when_allowed(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 100_000},
            total_equity=1_000_000,
        )
        audit_repo = MagicMock()
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
            audit_log_repo=audit_repo,
        )
        svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=10, limit_price=100), NOW)
        audit_repo.record_operator_action.assert_not_called()

    def test_missing_metadata_counter_incremented(self):
        reader = _make_sector_reader(
            symbol_exposures={},
            sector_map={"AAPL": "Technology"},
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
            unknown_sector_policy="warn_allow",
        )
        with patch(
            "autonomous_trading_platform.safety.services.pre_trade_risk_service.metrics"
        ) as mock_metrics:
            mock_metrics.missing_sector_metadata = MagicMock()
            mock_metrics.sector_exposure_pct = MagicMock()
            mock_metrics.sector_limit_utilization = MagicMock()
            mock_metrics.sector_concentration_blocks = MagicMock()

            svc._assert_sector_concentration(
                order_intent=_make_intent(symbol="TSLA"),
                order_notional=1_000.0,
                now=NOW,
            )
            mock_metrics.missing_sector_metadata.add.assert_called_once_with(1, {"symbol": "TSLA"})

    def test_sector_block_counter_incremented(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 390_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        with patch(
            "autonomous_trading_platform.safety.services.pre_trade_risk_service.metrics"
        ) as mock_metrics:
            mock_metrics.missing_sector_metadata = MagicMock()
            mock_metrics.sector_exposure_pct = MagicMock()
            mock_metrics.sector_limit_utilization = MagicMock()
            mock_metrics.sector_concentration_blocks = MagicMock()

            with pytest.raises(SectorConcentrationLimitExceededError):
                svc._assert_sector_concentration(
                    order_intent=_make_intent(symbol="AAPL", qty=100, limit_price=1_000),
                    order_notional=100_000.0,
                    now=NOW,
                )
            mock_metrics.sector_concentration_blocks.add.assert_called_once_with(
                1, {"sector": "Technology"}
            )


# ===========================================================================
# Existing checks unaffected
# ===========================================================================


class TestExistingChecksUnaffected:
    def test_gross_exposure_check_still_runs(self):
        svc = PreTradeRiskService(
            settings=_make_settings(max_gross_exposure=50_000.0),
            risk_state_reader=StubRiskStateReader(),
        )
        with pytest.raises(GrossExposureLimitExceededError):
            svc.assert_order_allowed(
                _make_intent(symbol="AAPL", qty=1000, limit_price=100),
                NOW,
            )

    def test_symbol_exposure_check_still_runs(self):
        svc = PreTradeRiskService(
            settings=_make_settings(max_symbol_exposure=5_000.0),
            risk_state_reader=StubRiskStateReader(),
        )
        with pytest.raises(SymbolExposureLimitExceededError):
            svc.assert_order_allowed(
                _make_intent(symbol="AAPL", qty=100, limit_price=100),
                NOW,
            )

    def test_sector_check_runs_after_existing_checks(self):
        reader = _make_sector_reader(
            symbol_exposures={"AAPL": 390_000},
            total_equity=1_000_000,
        )
        svc = _make_service(
            sector_reader=reader,
            max_sector_exposure_pct={"Technology": 0.40},
        )
        # The order is small, existing checks pass, sector check fires
        with pytest.raises(SectorConcentrationLimitExceededError):
            svc.assert_order_allowed(_make_intent(symbol="AAPL", qty=100, limit_price=1_000), NOW)


# ===========================================================================
# RiskSnapshotService — sector breakdown in snapshot
# ===========================================================================


class TestRiskSnapshotSectorBreakdown:
    def _make_position_snapshot(self):
        from autonomous_trading_platform.contracts.accounting.position_snapshot import (
            Position,
            PositionSnapshot,
        )
        from autonomous_trading_platform.contracts.common.enums import OrderSource

        return PositionSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            timestamp=NOW,
            source=OrderSource.LEDGER,
            positions=[
                Position(
                    symbol="AAPL",
                    quantity=Decimal("1000"),
                    avg_cost=Decimal("150"),
                    market_price=Decimal("200"),
                    market_value=Decimal("200000"),
                    unrealized_pnl=Decimal("50000"),
                ),
                Position(
                    symbol="JPM",
                    quantity=Decimal("500"),
                    avg_cost=Decimal("120"),
                    market_price=Decimal("150"),
                    market_value=Decimal("75000"),
                    unrealized_pnl=Decimal("15000"),
                ),
            ],
        )

    def _make_cash_snapshot(self):
        from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
        from autonomous_trading_platform.contracts.common.enums import OrderSource

        return CashSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            timestamp=NOW,
            source=OrderSource.LEDGER,
            currency="USD",
            cash=Decimal("725000"),
            equity=Decimal("1000000"),
            buying_power=Decimal("725000"),
            reserved_cash=Decimal("0"),
        )

    def test_snapshot_includes_sector_breakdown(self):
        svc = RiskSnapshotService()
        pos = self._make_position_snapshot()
        cash = self._make_cash_snapshot()
        limits = RiskLimitConfig(
            max_gross_exposure=Decimal("500000"),
            max_sector_exposure_pct={"Technology": 0.40, "Financials": 0.25},
        )
        sector_reader = SectorExposureReader(
            symbol_exposures_usd={"AAPL": Decimal("200000"), "JPM": Decimal("75000")},
            symbol_sector_map=_SECTOR_MAP,
            total_equity=Decimal("1000000"),
        )
        snapshot = svc.compute_snapshot(
            run_id=uuid.uuid4(),
            timestamp=NOW,
            position_snapshot=pos,
            cash_snapshot=cash,
            limits_config=limits,
            sector_exposure_reader=sector_reader,
        )
        assert "sector_concentration" in snapshot.utilization
        sector_data = snapshot.utilization["sector_concentration"]
        assert "sector_exposures" in sector_data
        assert "Technology" in sector_data["sector_exposures"]
        tech = sector_data["sector_exposures"]["Technology"]
        assert tech["exposure_usd"] == pytest.approx(200_000.0)
        assert tech["exposure_pct"] == pytest.approx(0.20)
        assert tech["limit_pct"] == pytest.approx(0.40)

    def test_snapshot_without_sector_reader_has_no_sector_key(self):
        svc = RiskSnapshotService()
        snapshot = svc.compute_snapshot(
            run_id=uuid.uuid4(),
            timestamp=NOW,
            position_snapshot=None,
            cash_snapshot=None,
            limits_config=RiskLimitConfig(),
        )
        assert "sector_concentration" not in snapshot.utilization

    def test_snapshot_limits_dict_includes_sector_config(self):
        svc = RiskSnapshotService()
        limits = RiskLimitConfig(
            max_sector_exposure_pct={"Technology": 0.35},
            default_max_sector_exposure_pct=0.25,
            unknown_sector_policy="warn_allow",
        )
        snapshot = svc.compute_snapshot(
            run_id=uuid.uuid4(),
            timestamp=NOW,
            position_snapshot=None,
            cash_snapshot=None,
            limits_config=limits,
        )
        assert snapshot.limits["max_sector_exposure_pct"] == {"Technology": 0.35}
        assert snapshot.limits["default_max_sector_exposure_pct"] == pytest.approx(0.25)
        assert snapshot.limits["unknown_sector_policy"] == "warn_allow"

    def test_snapshot_sector_near_and_over_limit_flagged(self):
        svc = RiskSnapshotService()
        sector_reader = SectorExposureReader(
            symbol_exposures_usd={"AAPL": Decimal("420000"), "JPM": Decimal("210000")},
            symbol_sector_map=_SECTOR_MAP,
            total_equity=Decimal("1000000"),
        )
        limits = RiskLimitConfig(
            max_sector_exposure_pct={"Technology": 0.40, "Financials": 0.25},
        )
        snapshot = svc.compute_snapshot(
            run_id=uuid.uuid4(),
            timestamp=NOW,
            position_snapshot=None,
            cash_snapshot=None,
            limits_config=limits,
            sector_exposure_reader=sector_reader,
        )
        sector_data = snapshot.utilization["sector_concentration"]
        # Technology at 42% > 40% → over limit
        assert "Technology" in sector_data["over_limit_sectors"]
        # Financials at 21% / 25% = 84% → near limit
        assert "Financials" in sector_data["near_limit_sectors"]


# ===========================================================================
# RiskLimitConfig
# ===========================================================================


class TestRiskLimitConfig:
    def test_default_sector_fields(self):
        config = RiskLimitConfig()
        assert config.max_sector_exposure_pct == {}
        assert config.default_max_sector_exposure_pct is None
        assert config.unknown_sector_policy == "reject"

    def test_sector_fields_set(self):
        config = RiskLimitConfig(
            max_sector_exposure_pct={"Technology": 0.35},
            default_max_sector_exposure_pct=0.20,
            unknown_sector_policy="use_unknown_bucket",
        )
        assert config.max_sector_exposure_pct == {"Technology": 0.35}
        assert config.default_max_sector_exposure_pct == pytest.approx(0.20)
        assert config.unknown_sector_policy == "use_unknown_bucket"
