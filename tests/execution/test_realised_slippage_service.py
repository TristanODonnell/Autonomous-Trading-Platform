from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    Side,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.realised_slippage_service import (
    RealisedSlippageService,
)
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics
from autonomous_trading_platform.storage.sor.repositories.core.fill_quality_metrics_repository import (
    FillQualityMetricsRepository,
)

_INTENT_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000002")
_NOW = datetime(2025, 1, 1, 15, 0, tzinfo=UTC)
_FILL_TS = datetime(2025, 1, 1, 15, 1, tzinfo=UTC)


def _make_fill(
    *,
    fill_id: str = "fill-001",
    intent_id: UUID = _INTENT_ID,
    side: Side = Side.BUY,
    price: str = "101",
    quantity: str = "10",
) -> Fill:
    return Fill(
        fill_id=fill_id,
        broker_order_id="bo-001",
        intent_id=intent_id,
        run_id=_RUN_ID,
        timestamp=_FILL_TS,
        symbol="AAPL",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


def _make_phase1_row(
    *,
    intent_id: str = str(_INTENT_ID),
    fill_id: str | None = None,
    reference_price: Decimal = Decimal("100"),
    fill_price: Decimal | None = None,
    fill_timestamp=None,
    policy_mode: str = "passthrough",
) -> FillQualityMetrics:
    return FillQualityMetrics(
        record_id="rec-001",
        run_id=str(_RUN_ID),
        intent_id=intent_id,
        fill_id=fill_id,
        strategy_id="strat-alpha",
        symbol="AAPL",
        signal_timestamp=_NOW,
        submission_timestamp=_NOW,
        fill_timestamp=fill_timestamp,
        submission_latency_seconds=0.05,
        fill_latency_seconds=None,
        reference_price=reference_price,
        fill_price=fill_price,
        expected_fill_price=Decimal("100.50"),
        slippage_per_share=None,
        slippage_notional=None,
        slippage_bps=None,
        fill_vs_expected_bps=None,
        commission_cost=None,
        spread_cost=None,
        slippage_cost=None,
        total_cost=None,
        policy_mode=policy_mode,
        quantity=Decimal("10"),
        is_adverse_fill=None,
        policy_metadata=None,
    )


class TrackingFillQualityMetricsRepository:
    """In-memory repo that tracks calls for assertion."""

    def __init__(self, seed: FillQualityMetrics | None = None) -> None:
        self._row: FillQualityMetrics | None = seed
        self.insert_calls: list[FillQualityMetrics] = []
        self.upsert_calls: list[FillQualityMetrics] = []

    def get_by_intent_id(self, intent_id: str) -> FillQualityMetrics | None:
        if self._row is not None and self._row.intent_id == intent_id:
            return self._row
        return None

    def get_by_record_id(self, record_id: str) -> FillQualityMetrics | None:
        if self._row is not None and self._row.record_id == record_id:
            return self._row
        return None

    def insert(self, row: FillQualityMetrics) -> None:
        self._row = row
        self.insert_calls.append(row)

    def upsert(self, row: FillQualityMetrics) -> FillQualityMetrics:
        self._row = row
        self.upsert_calls.append(row)
        return row


class DummyFillQualityMetricsRepository:
    def insert(self, *args, **kwargs):
        pass

    def upsert(self, *args, **kwargs):
        pass

    def upsert_by_intent_id(self, *args, **kwargs):
        pass

    def get_by_intent_id(self, *args, **kwargs):
        return None

    def get_by_record_id(self, *args, **kwargs):
        return None


def _make_svc(repo: TrackingFillQualityMetricsRepository) -> RealisedSlippageService:
    return RealisedSlippageService(repository=cast(FillQualityMetricsRepository, repo))


@pytest.fixture
def slippage_service():
    return RealisedSlippageService(
        repository=cast(FillQualityMetricsRepository, DummyFillQualityMetricsRepository())
    )


def test_calculate_buy_slippage_positive_when_fill_above_reference(slippage_service) -> None:

    result = slippage_service.calculate(
        side=Side.BUY,
        reference_price=Money("100"),
        fill_price=Money("101.25"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("1.25")
    assert result.slippage_notional == Decimal("12.50")
    assert result.slippage_bps == Decimal("125")


def test_calculate_buy_slippage_negative_when_fill_below_reference(slippage_service) -> None:

    result = slippage_service.calculate(
        side=Side.BUY,
        reference_price=Money("100"),
        fill_price=Money("99.50"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("-0.50")
    assert result.slippage_notional == Decimal("-5.00")
    assert result.slippage_bps == Decimal("-50")


def test_calculate_sell_slippage_positive_when_fill_below_reference(slippage_service) -> None:

    result = slippage_service.calculate(
        side=Side.SELL,
        reference_price=Money("100"),
        fill_price=Money("99"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("1")
    assert result.slippage_notional == Decimal("10")
    assert result.slippage_bps == Decimal("100")


def test_calculate_sell_slippage_negative_when_fill_above_reference(slippage_service) -> None:

    result = slippage_service.calculate(
        side=Side.SELL,
        reference_price=Money("100"),
        fill_price=Money("100.75"),
        quantity=Quantity("10"),
    )

    assert result.slippage_per_share == Decimal("-0.75")
    assert result.slippage_notional == Decimal("-7.50")
    assert result.slippage_bps == Decimal("-75")


def test_calculate_notional_slippage_equals_quantity_times_per_share_slippage(
    slippage_service,
) -> None:

    result = slippage_service.calculate(
        side=Side.BUY,
        reference_price=Money("250"),
        fill_price=Money("251.20"),
        quantity=Quantity("7"),
    )

    assert result.slippage_per_share == Decimal("1.20")
    assert result.slippage_notional == Decimal("8.40")
    assert result.slippage_notional == result.slippage_per_share * Decimal("7")


@pytest.mark.parametrize(
    ("reference_price", "fill_price", "quantity", "expected_message"),
    [
        (Money("0"), Money("100"), Quantity("10"), "reference_price must be positive"),
        (Money("-1"), Money("100"), Quantity("10"), "reference_price must be positive"),
        (Money("100"), Money("0"), Quantity("10"), "fill_price must be positive"),
        (Money("100"), Money("-1"), Quantity("10"), "fill_price must be positive"),
        (Money("100"), Money("101"), Quantity("0"), "quantity must be positive"),
        (Money("100"), Money("101"), Quantity("-5"), "quantity must be positive"),
    ],
)
def test_calculate_raises_for_invalid_inputs(
    slippage_service,
    reference_price: Money,
    fill_price: Money,
    quantity: Quantity,
    expected_message: str,
) -> None:

    with pytest.raises(ValueError, match=expected_message):
        slippage_service.calculate(
            side=Side.BUY,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
        )


# ---------------------------------------------------------------------------
# F-04 — Two-phase persistence ordering and idempotency tests
# ---------------------------------------------------------------------------


class TestPhase2BeforePhase1:
    """Phase 2 (fill actuals) arrives before Phase 1 (submission context)."""

    def test_phase2_inserts_minimal_row_when_phase1_missing(self) -> None:
        repo = TrackingFillQualityMetricsRepository(seed=None)
        svc = _make_svc(repo)

        fill = _make_fill(fill_id="fill-001", price="101", quantity="10")
        svc.record_fill_actuals(fill=fill, now_utc=_NOW)

        assert len(repo.insert_calls) == 1
        row = repo.insert_calls[0]
        assert row.fill_id == "fill-001"
        assert row.fill_price == Decimal("101")
        assert row.slippage_per_share is not None

    def test_phase1_after_phase2_merges_submission_context(self) -> None:
        # Phase 2 has already inserted a minimal row.
        phase2_row = _make_phase1_row(
            fill_id="fill-001",
            fill_price=Decimal("101"),
            fill_timestamp=_FILL_TS,
            policy_mode="passthrough",
            reference_price=Decimal("101"),  # phase 2 used fill_price as reference
        )
        repo = TrackingFillQualityMetricsRepository(seed=phase2_row)
        svc = _make_svc(repo)

        # Build a minimal policy_result mock
        policy_result = MagicMock()
        policy_result.reference_price = Decimal("100")
        policy_result.policy_mode_applied.value = "twap"
        policy_result.expected_slippage = None
        policy_result.expected_cost = None
        policy_result.policy_metadata = None

        intent = MagicMock()
        intent.intent_id = _INTENT_ID
        intent.run_id = _RUN_ID
        intent.strategy_id = "strat-alpha"
        intent.symbol = "AAPL"
        intent.timestamp = _NOW
        intent.qty = Decimal("10")

        broker_order = MagicMock()
        broker_order.broker_order_id = "bo-001"

        svc.record_submission_context(
            intent=intent,
            broker_order=broker_order,
            policy_result=policy_result,
            submit_duration_seconds=0.05,
            now_utc=_NOW,
        )

        # Must have updated (upserted) the existing row, not inserted a new one.
        assert len(repo.insert_calls) == 0
        assert len(repo.upsert_calls) == 1
        updated = repo.upsert_calls[0]
        # Submission context merged in.
        assert updated.policy_mode == "twap"
        assert updated.reference_price == Decimal("100")
        # Fill actuals preserved — not overwritten.
        assert updated.fill_id == "fill-001"
        assert updated.fill_price == Decimal("101")
        assert updated.fill_timestamp == _FILL_TS

    def test_phase1_after_phase2_emits_warning(self, caplog) -> None:
        phase2_row = _make_phase1_row(fill_id="fill-001", fill_price=Decimal("101"))
        repo = TrackingFillQualityMetricsRepository(seed=phase2_row)
        svc = _make_svc(repo)

        policy_result = MagicMock()
        policy_result.reference_price = Decimal("100")
        policy_result.policy_mode_applied.value = "passthrough"
        policy_result.expected_slippage = None
        policy_result.expected_cost = None
        policy_result.policy_metadata = None

        intent = MagicMock()
        intent.intent_id = _INTENT_ID
        intent.run_id = _RUN_ID
        intent.strategy_id = "strat-alpha"
        intent.symbol = "AAPL"
        intent.timestamp = _NOW
        intent.qty = None

        broker_order = MagicMock()
        broker_order.broker_order_id = "bo-001"

        import logging

        with caplog.at_level(logging.WARNING):
            svc.record_submission_context(
                intent=intent,
                broker_order=broker_order,
                policy_result=policy_result,
                submit_duration_seconds=0.05,
                now_utc=_NOW,
            )

        assert any("phase_ordering_anomaly" in r.message for r in caplog.records)

    def test_phase2_before_phase1_emits_warning(self, caplog) -> None:
        repo = TrackingFillQualityMetricsRepository(seed=None)
        svc = _make_svc(repo)

        fill = _make_fill()
        import logging

        with caplog.at_level(logging.WARNING):
            svc.record_fill_actuals(fill=fill, now_utc=_NOW)

        assert any("phase_ordering_anomaly" in r.message for r in caplog.records)


class TestDuplicatePhaseWrites:
    """Repeated Phase 1 or Phase 2 calls must not corrupt or duplicate data."""

    def test_repeated_phase1_updates_not_inserts(self) -> None:
        repo = TrackingFillQualityMetricsRepository(seed=None)
        svc = _make_svc(repo)

        policy_result = MagicMock()
        policy_result.reference_price = Decimal("100")
        policy_result.policy_mode_applied.value = "passthrough"
        policy_result.expected_slippage = None
        policy_result.expected_cost = None
        policy_result.policy_metadata = None

        intent = MagicMock()
        intent.intent_id = _INTENT_ID
        intent.run_id = _RUN_ID
        intent.strategy_id = "strat-alpha"
        intent.symbol = "AAPL"
        intent.timestamp = _NOW
        intent.qty = None

        broker_order = MagicMock()
        broker_order.broker_order_id = "bo-001"

        # First call inserts.
        svc.record_submission_context(
            intent=intent,
            broker_order=broker_order,
            policy_result=policy_result,
            submit_duration_seconds=0.01,
            now_utc=_NOW,
        )
        assert len(repo.insert_calls) == 1

        # Second call finds existing row and upserts (no second insert).
        svc.record_submission_context(
            intent=intent,
            broker_order=broker_order,
            policy_result=policy_result,
            submit_duration_seconds=0.02,
            now_utc=_NOW,
        )
        assert len(repo.insert_calls) == 1  # still 1
        assert len(repo.upsert_calls) == 1

    def test_repeated_phase2_updates_fill_fields(self) -> None:
        # Seed with a Phase 1 row (no fill yet).
        phase1_row = _make_phase1_row()
        repo = TrackingFillQualityMetricsRepository(seed=phase1_row)
        svc = _make_svc(repo)

        fill_a = _make_fill(fill_id="fill-001", price="101")
        fill_b = _make_fill(fill_id="fill-002", price="101.50")

        svc.record_fill_actuals(fill=fill_a, now_utc=_NOW)
        svc.record_fill_actuals(fill=fill_b, now_utc=_NOW)

        # Both calls should update (upsert), not insert.
        assert len(repo.insert_calls) == 0
        assert len(repo.upsert_calls) == 2
        # Latest fill data wins.
        assert repo._row is not None
        assert repo._row.fill_id == "fill-002"
        assert repo._row.fill_price == Decimal("101.50")

    def test_repeated_fill_update_emits_warning(self, caplog) -> None:
        phase1_row = _make_phase1_row(fill_id="fill-001", fill_price=Decimal("101"))
        repo = TrackingFillQualityMetricsRepository(seed=phase1_row)
        svc = _make_svc(repo)

        fill = _make_fill(fill_id="fill-002", price="102")
        import logging

        with caplog.at_level(logging.WARNING):
            svc.record_fill_actuals(fill=fill, now_utc=_NOW)

        assert any("repeated_fill_update" in r.message for r in caplog.records)


class TestMergedRecordCompleteness:
    """After both phases execute (in any order), the final row must be complete."""

    def test_phase1_then_phase2_produces_complete_record(self) -> None:
        repo = TrackingFillQualityMetricsRepository(seed=None)
        svc = _make_svc(repo)

        policy_result = MagicMock()
        policy_result.reference_price = Decimal("100")
        policy_result.policy_mode_applied.value = "passthrough"
        policy_result.expected_slippage = None
        policy_result.expected_cost = None
        policy_result.policy_metadata = None

        intent = MagicMock()
        intent.intent_id = _INTENT_ID
        intent.run_id = _RUN_ID
        intent.strategy_id = "strat-alpha"
        intent.symbol = "AAPL"
        intent.timestamp = _NOW
        intent.qty = Decimal("10")

        broker_order = MagicMock()
        broker_order.broker_order_id = "bo-001"

        svc.record_submission_context(
            intent=intent,
            broker_order=broker_order,
            policy_result=policy_result,
            submit_duration_seconds=0.05,
            now_utc=_NOW,
        )
        svc.record_fill_actuals(fill=_make_fill(price="101"), now_utc=_NOW)

        row = repo._row
        assert row is not None
        assert row.reference_price == Decimal("100")
        assert row.policy_mode == "passthrough"
        assert row.fill_price == Decimal("101")
        assert row.slippage_bps is not None
        assert row.is_adverse_fill is not None

    def test_phase2_then_phase1_produces_complete_record(self) -> None:
        repo = TrackingFillQualityMetricsRepository(seed=None)
        svc = _make_svc(repo)

        # Phase 2 first.
        svc.record_fill_actuals(fill=_make_fill(price="101"), now_utc=_NOW)

        # Phase 1 second.
        policy_result = MagicMock()
        policy_result.reference_price = Decimal("100")
        policy_result.policy_mode_applied.value = "twap"
        policy_result.expected_slippage = None
        policy_result.expected_cost = None
        policy_result.policy_metadata = None

        intent = MagicMock()
        intent.intent_id = _INTENT_ID
        intent.run_id = _RUN_ID
        intent.strategy_id = "strat-alpha"
        intent.symbol = "AAPL"
        intent.timestamp = _NOW
        intent.qty = Decimal("10")

        broker_order = MagicMock()
        broker_order.broker_order_id = "bo-001"

        svc.record_submission_context(
            intent=intent,
            broker_order=broker_order,
            policy_result=policy_result,
            submit_duration_seconds=0.05,
            now_utc=_NOW,
        )

        row = repo._row
        assert row is not None
        # Submission context merged in.
        assert row.policy_mode == "twap"
        assert row.reference_price == Decimal("100")
        # Fill actuals still intact.
        assert row.fill_price == Decimal("101")
        assert row.fill_timestamp == _FILL_TS
