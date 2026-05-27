# tests/execution/test_volatility_scaling_integration.py
"""
End-to-end tests for FINDING-18: Complete Volatility Scalar Integration.

Covers:
  - vol_scalar and sharpe_scalar computed and passed through to PositionSizer
  - combined scalar reduces notional correctly
  - scalar clamp prevents notional expansion
  - live mode raises MissingPositionScalingDataError when scalars absent
  - paper mode emits POSITION_SCALING_ABSENT and warns
  - simulation mode falls back silently (no error, no metric)
  - diagnostics fields present in SizingResult and OrderIntent.metadata
  - high-volatility scenario reduces position size
  - stale/missing vol data does not silently produce full-size live order
  - VolatilityScalingConfig disabled preserves pre-FINDING-18 full-size behavior
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.execution.services.position_sizer import (
    PositionSizer,
    SizingResult,
)
from autonomous_trading_platform.execution.services.sharpe_scaling_service import (
    SharpeScalingService,
)
from autonomous_trading_platform.execution.services.volatility_scaling_config import (
    VolatilityScalingConfig,
)
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
from autonomous_trading_platform.portfolio.exceptions import MissingPositionScalingDataError
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

STRATEGY = "test-strategy"
SYMBOL = "AAPL"
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
BAR_TS = datetime(2025, 6, 1, 14, 30, tzinfo=UTC)
NOW = datetime(2025, 6, 1, 14, 30, 45, tzinfo=UTC)

ALLOCATED = Decimal("10000.00")
ONE = Decimal("1")
ZERO = Decimal("0")


def make_buy_signal(symbol: str = SYMBOL) -> Signal:
    return Signal(
        signal_id=UUID("00000000-0000-0000-0000-000000000010"),
        run_id=RUN_ID,
        timestamp=BAR_TS,
        bar_timestamp=BAR_TS,
        strategy_id=STRATEGY,
        symbol=symbol,
        direction=SignalDirection.BUY,
        confidence=1.0,
    )


def flat_closes(price: float, n: int = 25) -> list[float]:
    """Zero-variance closes → extremely low realized vol → vol_scalar ≈ 1.0."""
    return [price] * n


def rising_closes(start: float, pct_per_bar: float, n: int = 25) -> list[float]:
    return [start * (1 + pct_per_bar) ** i for i in range(n)]


def volatile_closes(base: float, std_pct: float, n: int = 25) -> list[float]:
    """Deterministic high-vol series using alternating ±std_pct moves."""
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] * (1 + std_pct if i % 2 == 0 else 1 - std_pct))
    return closes


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------


@dataclass
class FakeAllocation:
    allocated_capital_usd: float = 10_000.0
    max_drawdown_allowed: float = 0.10
    max_position_size_usd: float | None = None


class FakeAllocationProvider:
    def get_allocation(self, **_kwargs) -> FakeAllocation:
        return FakeAllocation()


class PassthroughRiskService(PreTradeRiskService):
    def __init__(self) -> None:
        pass  # bypass PreTradeRiskService.__init__ in tests

    def assert_order_allowed(self, order_intent, now):
        pass


def make_real_sizer(
    capital_fraction: Decimal = ONE,
    max_symbol_exposure: Decimal | None = None,
) -> PositionSizer:
    return PositionSizer(
        portfolio_engine=cast(IAllocationProvider, FakeAllocationProvider()),
        capital_fraction=capital_fraction,
        max_symbol_exposure_usd=max_symbol_exposure,
    )


def make_service(
    *,
    vol_service: VolatilityScalingService | None = None,
    sharpe_service: SharpeScalingService | None = None,
    vol_config: VolatilityScalingConfig | None = None,
    trading_environment: TradingEnvironment | None = None,
    capital_fraction: Decimal = ONE,
) -> PortfolioConstructionService:
    return PortfolioConstructionService(
        pre_trade_risk_service=PassthroughRiskService(),
        position_sizer=make_real_sizer(capital_fraction=capital_fraction),
        volatility_scaling_service=vol_service,
        sharpe_scaling_service=sharpe_service,
        volatility_scaling_config=vol_config,
        trading_environment=trading_environment,
    )


def generate_intents(
    service: PortfolioConstructionService,
    recent_closes: dict[str, list[float]] | None,
    prices: dict[str, float] | None = None,
    symbol: str = SYMBOL,
) -> list:
    return list(
        service.generate_order_intents(
            signals=[make_buy_signal(symbol)],
            positions={},
            prices=prices or {symbol: 100.0},
            run_id=RUN_ID,
            strategy_id=STRATEGY,
            bar_timestamp=BAR_TS,
            now=NOW,
            approval_status=GovernanceState.APPROVED_LIVE,
            recent_closes=recent_closes,
        )
    )


# ---------------------------------------------------------------------------
# Tests: SizingResult structure (PositionSizer unit)
# ---------------------------------------------------------------------------


class TestSizingResult:
    def test_returns_sizing_result_with_quantity(self) -> None:
        sizer = make_real_sizer()
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
            combined_scalar=Decimal("0.5"),
        )
        assert isinstance(result, SizingResult)
        assert result.quantity == 50  # 10000 * 0.5 / 100

    def test_base_notional_unaffected_by_scalar(self) -> None:
        sizer = make_real_sizer()
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
            combined_scalar=Decimal("0.5"),
        )
        assert result.base_notional == ALLOCATED
        assert result.final_notional == ALLOCATED * Decimal("0.5")

    def test_scaling_applied_true_when_scalar_reduces_notional(self) -> None:
        sizer = make_real_sizer()
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
            combined_scalar=Decimal("0.7"),
        )
        assert result.scaling_applied is True

    def test_scaling_applied_false_when_no_scalar(self) -> None:
        sizer = make_real_sizer()
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
        )
        assert result.scaling_applied is False
        assert result.combined_scalar is None

    def test_combined_scalar_stored_on_result(self) -> None:
        scalar = Decimal("0.6")
        sizer = make_real_sizer()
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
            combined_scalar=scalar,
        )
        assert result.combined_scalar == scalar

    def test_zero_return_paths_include_notional_fields(self) -> None:
        sizer = PositionSizer(
            portfolio_engine=cast(IAllocationProvider, FakeAllocationProvider()),
            min_notional_usd=Decimal("99999"),  # force below-min
        )
        result = sizer.compute_quantity(
            strategy_id=STRATEGY,
            symbol=SYMBOL,
            current_price=Decimal("100"),
        )
        assert result.quantity == 0
        assert result.base_notional > ZERO
        assert result.final_notional >= ZERO

    def test_invalid_combined_scalar_raises(self) -> None:
        sizer = make_real_sizer()
        with pytest.raises(ValueError, match="combined_scalar"):
            sizer.compute_quantity(
                strategy_id=STRATEGY,
                symbol=SYMBOL,
                current_price=Decimal("100"),
                combined_scalar=Decimal("1.5"),
            )

    def test_zero_combined_scalar_raises(self) -> None:
        sizer = make_real_sizer()
        with pytest.raises(ValueError, match="combined_scalar"):
            sizer.compute_quantity(
                strategy_id=STRATEGY,
                symbol=SYMBOL,
                current_price=Decimal("100"),
                combined_scalar=ZERO,
            )


# ---------------------------------------------------------------------------
# Tests: ScalarResult from _compute_combined_scalar
# ---------------------------------------------------------------------------


class TestScalarComputation:
    def _svc_with_vol(self, **kw) -> PortfolioConstructionService:
        return make_service(
            vol_service=VolatilityScalingService(**kw),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=True),
        )

    def test_no_services_returns_no_scaling_services_reason(self) -> None:
        svc = make_service(vol_config=VolatilityScalingConfig())
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: flat_closes(100.0)})
        assert result.combined_scalar is None
        assert result.scaling_missing_reason == "no_scaling_services"

    def test_scaling_disabled_returns_disabled_reason(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=False),
        )
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: flat_closes(100.0)})
        assert result.combined_scalar is None
        assert result.scaling_missing_reason == "scaling_disabled"

    def test_no_closes_data_returns_no_closes_data_reason(self) -> None:
        svc = self._svc_with_vol()
        result = svc._compute_combined_scalar(SYMBOL, None)
        assert result.combined_scalar is None
        assert result.scaling_missing_reason == "no_closes_data"

    def test_missing_symbol_closes_returns_no_symbol_closes_reason(self) -> None:
        svc = self._svc_with_vol()
        result = svc._compute_combined_scalar(SYMBOL, {"OTHER": flat_closes(100.0)})
        assert result.combined_scalar is None
        assert result.scaling_missing_reason == "no_symbol_closes"

    def test_insufficient_bars_returns_insufficient_bars_reason(self) -> None:
        svc = self._svc_with_vol(min_bars=20)
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: flat_closes(100.0, n=5)})
        assert result.combined_scalar is None
        assert result.scaling_missing_reason == "insufficient_bars"

    def test_valid_closes_returns_combined_scalar(self) -> None:
        svc = self._svc_with_vol()
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: volatile_closes(100.0, 0.01)})
        assert result.combined_scalar is not None
        assert ZERO < result.combined_scalar <= ONE

    def test_vol_and_sharpe_both_populated(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            sharpe_service=SharpeScalingService(),
            vol_config=VolatilityScalingConfig(),
        )
        closes = rising_closes(100.0, 0.002, n=25)
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: closes})
        assert result.vol_scalar is not None
        assert result.sharpe_scalar is not None
        assert result.combined_scalar is not None
        assert result.scaling_missing_reason is None

    def test_combined_is_product_of_individual_scalars(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            sharpe_service=SharpeScalingService(),
            vol_config=VolatilityScalingConfig(),
        )
        closes = volatile_closes(100.0, 0.015, n=25)
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: closes})
        if result.vol_scalar is not None and result.sharpe_scalar is not None:
            expected = min(result.vol_scalar * result.sharpe_scalar, ONE)
            assert result.combined_scalar == expected


# ---------------------------------------------------------------------------
# Tests: Scalar clamp semantics
# ---------------------------------------------------------------------------


class TestScalarClamp:
    def test_combined_scalar_never_exceeds_one(self) -> None:
        """Even if a mock service returns exactly 1.0 for both, product stays ≤ 1.0."""
        vol_svc = MagicMock(spec=VolatilityScalingService)
        vol_svc.compute_scalar.return_value = ONE
        sharpe_svc = MagicMock(spec=SharpeScalingService)
        sharpe_svc.compute_scalar.return_value = ONE

        svc = make_service(
            vol_service=cast(VolatilityScalingService, vol_svc),
            sharpe_service=cast(SharpeScalingService, sharpe_svc),
            vol_config=VolatilityScalingConfig(),
        )
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: flat_closes(100.0)})
        assert result.combined_scalar is not None
        assert result.combined_scalar <= ONE

    def test_high_vol_reduces_notional_below_base(self) -> None:
        """Volatile closes should produce a scalar < 1.0, reducing position size."""
        svc = make_service(
            vol_service=VolatilityScalingService(target_annual_vol=0.10),
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        # 3 % per-bar swings → high annualised vol → scalar << 1.0
        closes = volatile_closes(100.0, 0.03, n=25)
        result = svc._compute_combined_scalar(SYMBOL, {SYMBOL: closes})
        assert result.combined_scalar is not None
        assert result.combined_scalar < ONE

    def test_position_size_smaller_in_high_vol(self) -> None:
        # Use only vol service so Sharpe doesn't collapse both cases to floor.
        # Low price ($1) gives enough shares even after vol scaling.
        vol_svc = VolatilityScalingService(target_annual_vol=0.10)
        svc_paper = make_service(
            vol_service=vol_svc,
            sharpe_service=None,
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        high_vol_closes = volatile_closes(1.0, 0.02, n=25)  # high annualised vol
        low_vol_closes = volatile_closes(1.0, 0.0001, n=25)  # near-zero vol

        intents_high = generate_intents(svc_paper, {SYMBOL: high_vol_closes}, prices={SYMBOL: 1.0})
        intents_low = generate_intents(svc_paper, {SYMBOL: low_vol_closes}, prices={SYMBOL: 1.0})

        qty_high = int(intents_high[0].qty) if intents_high else 0
        qty_low = int(intents_low[0].qty) if intents_low else 0
        assert qty_high < qty_low, "High-vol regime must produce a smaller position"


# ---------------------------------------------------------------------------
# Tests: Live mode fail-closed
# ---------------------------------------------------------------------------


class TestLiveModeFailClosed:
    def _live_svc(self, **vol_config_kw) -> PortfolioConstructionService:
        return make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(**vol_config_kw),
            trading_environment=TradingEnvironment.LIVE,
        )

    def test_raises_when_no_closes_data(self) -> None:
        svc = self._live_svc()
        with pytest.raises(MissingPositionScalingDataError) as exc_info:
            generate_intents(svc, recent_closes=None)
        assert exc_info.value.symbol == SYMBOL
        assert exc_info.value.trading_mode == "live"
        assert "no_closes_data" in exc_info.value.reason

    def test_raises_when_symbol_has_no_closes(self) -> None:
        svc = self._live_svc()
        with pytest.raises(MissingPositionScalingDataError) as exc_info:
            generate_intents(svc, recent_closes={"OTHER": flat_closes(100.0)})
        assert "no_symbol_closes" in exc_info.value.reason

    def test_raises_when_insufficient_bars(self) -> None:
        svc = self._live_svc()
        with pytest.raises(MissingPositionScalingDataError) as exc_info:
            generate_intents(svc, recent_closes={SYMBOL: flat_closes(100.0, n=5)})
        assert "insufficient_bars" in exc_info.value.reason

    def test_raises_strategy_id_matches(self) -> None:
        svc = self._live_svc()
        with pytest.raises(MissingPositionScalingDataError) as exc_info:
            generate_intents(svc, recent_closes=None)
        assert exc_info.value.strategy_id == STRATEGY

    def test_does_not_raise_when_require_vol_scalar_disabled(self) -> None:
        svc = self._live_svc(require_vol_scalar_for_live=False)
        intents = generate_intents(svc, recent_closes=None)
        assert len(intents) == 1  # full-size order placed (with warning)

    def test_does_not_raise_when_vol_service_not_configured(self) -> None:
        """No scaling service → enforcement inactive → no raise."""
        svc = make_service(
            vol_service=None,
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.LIVE,
        )
        intents = generate_intents(svc, recent_closes=None)
        assert len(intents) == 1

    def test_valid_closes_do_not_raise(self) -> None:
        svc = self._live_svc()
        closes = volatile_closes(100.0, 0.01, n=25)
        intents = generate_intents(svc, recent_closes={SYMBOL: closes})
        assert len(intents) == 1

    def test_stale_vol_data_does_not_silently_produce_full_size(self) -> None:
        """
        A None recent_closes (e.g. bar fetch failed) must raise, not silently
        size at full notional, when live + require_vol_scalar_for_live=True.
        """
        svc = self._live_svc()
        with pytest.raises(MissingPositionScalingDataError):
            generate_intents(svc, recent_closes=None)


# ---------------------------------------------------------------------------
# Tests: Paper mode — warn + audit, no raise
# ---------------------------------------------------------------------------


class TestPaperModeAudit:
    def _paper_svc(self) -> PortfolioConstructionService:
        return make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )

    def test_no_raise_when_closes_missing(self) -> None:
        svc = self._paper_svc()
        intents = generate_intents(svc, recent_closes=None)
        assert len(intents) == 1

    def test_warning_logged_when_scalars_absent(self, caplog) -> None:
        import logging

        svc = self._paper_svc()
        with caplog.at_level(logging.WARNING):
            generate_intents(svc, recent_closes=None)
        assert any("POSITION_SCALING_ABSENT" in r.message for r in caplog.records)

    @patch(
        "autonomous_trading_platform.execution.services.portfolio_construction_service"
        ".ratp_position_scaling_absent_total"
    )
    def test_metric_emitted_when_base_notional_above_threshold(
        self, mock_metric: MagicMock
    ) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(
                min_position_scaling_notional_for_audit=Decimal("1.00")
            ),
            trading_environment=TradingEnvironment.PAPER,
        )
        generate_intents(svc, recent_closes=None)
        mock_metric.add.assert_called_once()
        call_args = mock_metric.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["trading_mode"] == "paper"
        assert attrs["reason"] == "no_closes_data"

    @patch(
        "autonomous_trading_platform.execution.services.portfolio_construction_service"
        ".ratp_position_scaling_absent_total"
    )
    def test_metric_not_emitted_below_notional_threshold(self, mock_metric: MagicMock) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(
                min_position_scaling_notional_for_audit=Decimal("9999999.00")
            ),
            trading_environment=TradingEnvironment.PAPER,
        )
        generate_intents(svc, recent_closes=None)
        mock_metric.add.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Simulation mode — silent deterministic fallback
# ---------------------------------------------------------------------------


class TestSimulationFallback:
    def _sim_svc(self) -> PortfolioConstructionService:
        return make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(),
            trading_environment=None,
        )

    def test_no_raise_no_warning_when_closes_missing(self, caplog) -> None:
        import logging

        svc = self._sim_svc()
        with caplog.at_level(logging.WARNING):
            intents = generate_intents(svc, recent_closes=None)
        assert len(intents) == 1
        assert not any("POSITION_SCALING_ABSENT" in r.message for r in caplog.records)

    @patch(
        "autonomous_trading_platform.execution.services.portfolio_construction_service"
        ".ratp_position_scaling_absent_total"
    )
    def test_metric_not_emitted_in_simulation(self, mock_metric: MagicMock) -> None:
        svc = self._sim_svc()
        generate_intents(svc, recent_closes=None)
        mock_metric.add.assert_not_called()

    def test_deterministic_fallback_when_no_closes(self) -> None:
        svc = self._sim_svc()
        intents_a = generate_intents(svc, recent_closes=None)
        intents_b = generate_intents(svc, recent_closes=None)
        assert intents_a[0].qty == intents_b[0].qty


# ---------------------------------------------------------------------------
# Tests: Sizing diagnostics in OrderIntent.metadata
# ---------------------------------------------------------------------------


class TestSizingDiagnostics:
    def _svc(
        self, env: TradingEnvironment | None = TradingEnvironment.PAPER
    ) -> PortfolioConstructionService:
        return make_service(
            vol_service=VolatilityScalingService(),
            sharpe_service=SharpeScalingService(),
            vol_config=VolatilityScalingConfig(),
            trading_environment=env,
        )

    def test_metadata_contains_sizing_key(self) -> None:
        svc = self._svc()
        closes = volatile_closes(100.0, 0.01, n=25)
        intents = generate_intents(svc, {SYMBOL: closes})
        assert intents[0].metadata is not None
        assert "sizing" in intents[0].metadata

    def test_sizing_metadata_has_all_required_fields(self) -> None:
        svc = self._svc()
        closes = volatile_closes(100.0, 0.01, n=25)
        intents = generate_intents(svc, {SYMBOL: closes})
        sizing = intents[0].metadata["sizing"]
        for key in (
            "base_notional",
            "scaled_notional",
            "vol_scalar",
            "sharpe_scalar",
            "combined_scalar",
            "scaling_applied",
            "scaling_missing_reason",
        ):
            assert key in sizing, f"Missing field: {key}"

    def test_scaling_applied_true_for_volatile_closes(self) -> None:
        # Use only vol service so Sharpe doesn't further reduce below 1 share.
        # 1 % per-bar swings on $10 k capital at $10/share gives plenty of headroom.
        svc = make_service(
            vol_service=VolatilityScalingService(target_annual_vol=0.10),
            sharpe_service=None,
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = volatile_closes(10.0, 0.01, n=25)
        intents = generate_intents(svc, {SYMBOL: closes}, prices={SYMBOL: 10.0})
        assert len(intents) == 1, "Expect at least 1 intent for affordable position"
        assert intents[0].metadata["sizing"]["scaling_applied"] is True

    def test_scaled_notional_less_than_base_when_scaling_applied(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(target_annual_vol=0.10),
            sharpe_service=None,
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = volatile_closes(10.0, 0.01, n=25)
        intents = generate_intents(svc, {SYMBOL: closes}, prices={SYMBOL: 10.0})
        if intents:
            sizing = intents[0].metadata["sizing"]
            if sizing["scaling_applied"]:
                assert sizing["scaled_notional"] < sizing["base_notional"]

    def test_combined_scalar_matches_product_of_individual_scalars(self) -> None:
        svc = self._svc()
        closes = volatile_closes(10.0, 0.005, n=25)
        intents = generate_intents(svc, {SYMBOL: closes}, prices={SYMBOL: 10.0})
        if intents:
            sizing = intents[0].metadata["sizing"]
            if sizing["vol_scalar"] is not None and sizing["sharpe_scalar"] is not None:
                expected = min(sizing["vol_scalar"] * sizing["sharpe_scalar"], 1.0)
                assert abs(sizing["combined_scalar"] - expected) < 1e-6

    def test_missing_scalar_reason_set_when_closes_absent(self) -> None:
        svc = self._svc(env=TradingEnvironment.PAPER)
        intents = generate_intents(svc, recent_closes=None)
        sizing = intents[0].metadata["sizing"]
        assert sizing["scaling_missing_reason"] == "no_closes_data"
        assert sizing["combined_scalar"] is None

    def test_metadata_also_contains_signal_fields(self) -> None:
        svc = self._svc(env=None)
        intents = generate_intents(svc, recent_closes=None)
        assert "signal_id" in intents[0].metadata
        assert "signal_generated_at" in intents[0].metadata


# ---------------------------------------------------------------------------
# Tests: Scaling disabled — backward compatibility
# ---------------------------------------------------------------------------


class TestScalingDisabledBackwardCompat:
    def test_no_scalar_applied_when_disabled(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=False),
            trading_environment=TradingEnvironment.LIVE,
        )
        closes = volatile_closes(100.0, 0.05, n=25)
        intents = generate_intents(svc, {SYMBOL: closes})
        assert len(intents) == 1
        sizing = intents[0].metadata["sizing"]
        assert sizing["combined_scalar"] is None
        assert sizing["scaling_missing_reason"] == "scaling_disabled"

    def test_no_live_raise_when_disabled(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=False),
            trading_environment=TradingEnvironment.LIVE,
        )
        intents = generate_intents(svc, recent_closes=None)
        assert len(intents) == 1

    def test_full_notional_used_when_disabled(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(target_annual_vol=0.01),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=False),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = volatile_closes(100.0, 0.05, n=25)
        intents_disabled = generate_intents(svc, {SYMBOL: closes})

        svc_enabled = make_service(
            vol_service=VolatilityScalingService(target_annual_vol=0.01),
            vol_config=VolatilityScalingConfig(volatility_scaling_enabled=True),
            trading_environment=TradingEnvironment.PAPER,
        )
        intents_enabled = generate_intents(svc_enabled, {SYMBOL: closes})

        qty_disabled = intents_disabled[0].qty if intents_disabled else 0
        qty_enabled = intents_enabled[0].qty if intents_enabled else 0
        assert qty_disabled > qty_enabled, "Disabled scaling must produce larger (full) size"


# ---------------------------------------------------------------------------
# Tests: vol_scalar and sharpe_scalar passed through independently
# ---------------------------------------------------------------------------


class TestScalarPropagation:
    def test_only_vol_service_produces_vol_scalar_in_metadata(self) -> None:
        svc = make_service(
            vol_service=VolatilityScalingService(),
            sharpe_service=None,
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = volatile_closes(100.0, 0.02, n=25)
        intents = generate_intents(svc, {SYMBOL: closes})
        sizing = intents[0].metadata["sizing"]
        assert sizing["vol_scalar"] is not None
        assert sizing["sharpe_scalar"] is None

    def test_only_sharpe_service_produces_sharpe_scalar_in_metadata(self) -> None:
        svc = make_service(
            vol_service=None,
            sharpe_service=SharpeScalingService(),
            vol_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = rising_closes(100.0, 0.001, n=25)
        intents = generate_intents(svc, {SYMBOL: closes})
        sizing = intents[0].metadata["sizing"]
        assert sizing["vol_scalar"] is None
        assert sizing["sharpe_scalar"] is not None

    def test_vol_scalar_not_overwritten_by_none(self) -> None:
        """When sharpe service is absent, the vol_scalar still reaches PositionSizer."""
        mock_sizer = MagicMock(spec=PositionSizer)
        base = Decimal("10000")
        mock_sizer.compute_quantity.return_value = SizingResult(
            quantity=50,
            base_notional=base,
            final_notional=base * Decimal("0.5"),
            combined_scalar=Decimal("0.5"),
            scaling_applied=True,
        )
        svc = PortfolioConstructionService(
            pre_trade_risk_service=PassthroughRiskService(),
            position_sizer=cast(PositionSizer, mock_sizer),
            volatility_scaling_service=VolatilityScalingService(),
            sharpe_scaling_service=None,
            volatility_scaling_config=VolatilityScalingConfig(),
            trading_environment=TradingEnvironment.PAPER,
        )
        closes = volatile_closes(100.0, 0.03, n=25)
        generate_intents(svc, {SYMBOL: closes})
        call_kwargs = mock_sizer.compute_quantity.call_args.kwargs
        assert call_kwargs["combined_scalar"] is not None
        assert ZERO < call_kwargs["combined_scalar"] <= ONE


# ---------------------------------------------------------------------------
# Tests: MissingPositionScalingDataError structure
# ---------------------------------------------------------------------------


class TestMissingPositionScalingDataError:
    def test_exception_carries_all_fields(self) -> None:
        exc = MissingPositionScalingDataError(
            strategy_id="strat-1",
            symbol="TSLA",
            trading_mode="live",
            reason="no_closes_data",
        )
        assert exc.strategy_id == "strat-1"
        assert exc.symbol == "TSLA"
        assert exc.trading_mode == "live"
        assert exc.reason == "no_closes_data"
        assert "TSLA" in str(exc)
        assert "live" in str(exc)

    def test_exception_is_portfolio_error(self) -> None:
        from autonomous_trading_platform.portfolio.exceptions import PortfolioError

        exc = MissingPositionScalingDataError("s", "SYM", "live", "reason")
        assert isinstance(exc, PortfolioError)


# ---------------------------------------------------------------------------
# Tests: VolatilityScalingConfig defaults and semantics
# ---------------------------------------------------------------------------


class TestVolatilityScalingConfig:
    def test_default_config_live_fail_closed(self) -> None:
        cfg = VolatilityScalingConfig()
        assert cfg.volatility_scaling_enabled is True
        assert cfg.require_vol_scalar_for_live is True
        assert cfg.allow_full_size_when_scalars_missing is False

    def test_custom_notional_threshold(self) -> None:
        cfg = VolatilityScalingConfig(min_position_scaling_notional_for_audit=Decimal("500.00"))
        assert cfg.min_position_scaling_notional_for_audit == Decimal("500.00")

    def test_disabled_config(self) -> None:
        cfg = VolatilityScalingConfig(volatility_scaling_enabled=False)
        assert cfg.volatility_scaling_enabled is False
