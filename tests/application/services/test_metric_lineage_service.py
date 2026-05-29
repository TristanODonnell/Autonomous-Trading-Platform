from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
    compute_alpha,
)
from autonomous_trading_platform.application.services.metric_lineage_service import (
    MetricLineageService,
    _live_quality_score,
    _research_quality_score,
)
from autonomous_trading_platform.contracts.common.enums import (
    OrderSource,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.runtime.live_metrics_summary import LiveMetricsSummary
from autonomous_trading_platform.contracts.runtime.metric_lineage import (
    MetricLineageMetadata,
    MetricLineageType,
)
from autonomous_trading_platform.contracts.runtime.research_metrics_summary import (
    ResearchMetricsSummary,
)
from autonomous_trading_platform.storage.sor.models.blended_metrics_snapshots import (
    BlendedMetricsSnapshot,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)

_RUN_UUID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_STRATEGY_ID = "test_strategy_lineage"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_metrics_summary(
    session: Session,
    *,
    run_uuid: UUID = _RUN_UUID,
    strategy_id: str = _STRATEGY_ID,
    sharpe: float = 1.2,
    drawdown: float = -0.08,
    total_return: float = 0.15,
    trade_count: int = 80,
    winning_trades: int = 48,
) -> str:
    snap_id = str(uuid4())
    session.add(
        MetricsSummary(
            metrics_snapshot_id=snap_id,
            run_id=str(run_uuid),
            created_at=datetime.now(UTC),
            sharpe_ratio=sharpe,
            max_drawdown=drawdown,
            total_return=total_return,
            trade_count=trade_count,
            winning_trade_count=winning_trades,
            losing_trade_count=trade_count - winning_trades,
            volatility=0.18,
            metrics_json={"strategy_id": strategy_id},
        )
    )
    session.flush()
    return snap_id


def _make_intent(session: Session, strategy_id: str, run_id: UUID = _RUN_UUID) -> UUID:
    intent_id = uuid4()
    session.add(
        OrderIntents(
            intent_id=intent_id,
            idempotency_key=str(intent_id),
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=datetime.now(UTC),
            bar_timestamp=datetime.now(UTC),
            symbol="AAPL",
            side=Side.BUY,
            qty=Decimal("10"),
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=str(uuid4()),
        )
    )
    session.flush()
    return intent_id


def _make_fill(
    session: Session,
    intent_id: UUID,
    *,
    side: Side,
    price: Decimal,
    qty: Decimal,
    ts_offset_days: int = 0,
    run_uuid: UUID = _RUN_UUID,
) -> None:
    session.add(
        Fill(
            fill_id=str(uuid4()),
            broker_order_id=str(uuid4()),
            intent_id=intent_id,
            run_id=run_uuid,
            symbol="AAPL",
            side=side,
            price=price,
            quantity=qty,
            timestamp=datetime.now(UTC) - timedelta(days=ts_offset_days),
        )
    )
    session.flush()


def _make_equity_curve(session: Session, run_uuid: UUID = _RUN_UUID, days: int = 30) -> None:
    base = 100_000.0
    for i in range(days):
        session.add(
            CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_uuid,
                timestamp=datetime.now(UTC) - timedelta(days=days - i),
                currency="USD",
                cash=Decimal(str(base + i * 100)),
                buying_power=Decimal(str(base + i * 100)),
                reserved_cash=Decimal("0"),
                equity=Decimal(str(base + i * 200)),
                source=OrderSource.LEDGER,
            )
        )
    session.flush()


# ---------------------------------------------------------------------------
# MetricLineageType enum
# ---------------------------------------------------------------------------


class TestMetricLineageType:
    def test_enum_values(self):
        assert MetricLineageType.RESEARCH.value == "research"
        assert MetricLineageType.LIVE.value == "live"
        assert MetricLineageType.BLENDED.value == "blended"

    def test_str_coercion(self):
        assert MetricLineageType("research") is MetricLineageType.RESEARCH
        assert MetricLineageType("live") is MetricLineageType.LIVE
        assert MetricLineageType("blended") is MetricLineageType.BLENDED


# ---------------------------------------------------------------------------
# MetricLineageMetadata
# ---------------------------------------------------------------------------


class TestMetricLineageMetadata:
    def test_research_metadata(self):
        now = datetime.now(UTC)
        meta = MetricLineageMetadata(
            metric_lineage_type=MetricLineageType.RESEARCH,
            environment="test",
            calculation_version="1.0",
            generated_at=now,
            source_run_ids=["run-1"],
            source_strategy_ids=["strat-1"],
        )
        assert meta.metric_lineage_type is MetricLineageType.RESEARCH
        assert meta.environment == "test"
        assert "run-1" in meta.source_run_ids

    def test_defaults(self):
        now = datetime.now(UTC)
        meta = MetricLineageMetadata(
            metric_lineage_type=MetricLineageType.LIVE,
            generated_at=now,
        )
        assert meta.source_run_ids == []
        assert meta.source_strategy_ids == []
        assert meta.lookback_window_days is None


# ---------------------------------------------------------------------------
# ResearchMetricsSummary
# ---------------------------------------------------------------------------


class TestResearchMetricsSummary:
    def test_win_rate_computed(self):
        now = datetime.now(UTC)
        r = ResearchMetricsSummary(
            metrics_snapshot_id="snap-1",
            run_id="run-1",
            created_at=now,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.RESEARCH, generated_at=now
            ),
            trade_count=100,
            winning_trade_count=60,
        )
        assert r.win_rate == pytest.approx(0.60)

    def test_win_rate_none_when_no_trades(self):
        now = datetime.now(UTC)
        r = ResearchMetricsSummary(
            metrics_snapshot_id="snap-1",
            run_id="run-1",
            created_at=now,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.RESEARCH, generated_at=now
            ),
        )
        assert r.win_rate is None

    def test_always_research_lineage_type(self):
        now = datetime.now(UTC)
        r = ResearchMetricsSummary(
            metrics_snapshot_id="snap-1",
            run_id="run-1",
            created_at=now,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.RESEARCH, generated_at=now
            ),
        )
        assert r.metric_lineage_type is MetricLineageType.RESEARCH


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------


class TestQualityScoreHelpers:
    def _research(self, **kwargs) -> ResearchMetricsSummary:
        now = datetime.now(UTC)
        return ResearchMetricsSummary(
            metrics_snapshot_id="x",
            run_id="r",
            created_at=now,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.RESEARCH, generated_at=now
            ),
            **kwargs,
        )

    def _live(self, **kwargs) -> LiveMetricsSummary:
        now = datetime.now(UTC)
        return LiveMetricsSummary(
            snapshot_id="x",
            strategy_id="s",
            computed_at=now,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.LIVE, generated_at=now
            ),
            **kwargs,
        )

    def test_research_score_positive_sharpe_increases_score(self):
        low = _research_quality_score(self._research(sharpe_ratio=0.0))
        high = _research_quality_score(self._research(sharpe_ratio=2.0))
        assert high > low

    def test_research_score_large_drawdown_reduces_score(self):
        small = _research_quality_score(self._research(max_drawdown=-0.05))
        large = _research_quality_score(self._research(max_drawdown=-0.30))
        assert large < small

    def test_research_score_minimum_is_enforced(self):
        score = _research_quality_score(
            self._research(sharpe_ratio=-5.0, max_drawdown=-0.99, total_return=-0.99)
        )
        assert score >= 0.01

    def test_live_score_positive_sharpe_increases(self):
        low = _live_quality_score(self._live(rolling_sharpe=0.0))
        high = _live_quality_score(self._live(rolling_sharpe=2.0))
        assert high > low

    def test_live_score_minimum_enforced(self):
        score = _live_quality_score(
            self._live(rolling_sharpe=-5.0, realized_drawdown=-0.99, realized_return=-0.99)
        )
        assert score >= 0.01


# ---------------------------------------------------------------------------
# compute_alpha
# ---------------------------------------------------------------------------


class TestComputeAlpha:
    def test_new_strategy_low_alpha(self):
        assert compute_alpha(days_live=0, trade_count=0) == pytest.approx(0.10)

    def test_mature_strategy_high_alpha(self):
        alpha = compute_alpha(days_live=100, trade_count=60)
        assert alpha >= 0.80

    def test_alpha_bottlenecked_by_trade_count(self):
        alpha = compute_alpha(days_live=200, trade_count=2)
        assert alpha == pytest.approx(0.10)

    def test_alpha_bottlenecked_by_days(self):
        alpha = compute_alpha(days_live=1, trade_count=200)
        assert alpha == pytest.approx(0.10)

    def test_alpha_monotone_with_days(self):
        alphas = [compute_alpha(days_live=d, trade_count=60) for d in [0, 10, 30, 60, 90, 120]]
        assert alphas == sorted(alphas)

    def test_alpha_bounded(self):
        for d, t in [(0, 0), (30, 30), (90, 50), (200, 200)]:
            a = compute_alpha(days_live=d, trade_count=t)
            assert 0.0 <= a <= 1.0


# ---------------------------------------------------------------------------
# MetricLineageService integration tests
# ---------------------------------------------------------------------------


class TestMetricLineageService:
    def test_resolve_research_metrics_returns_none_when_no_data(self, db_session: Session):
        svc = MetricLineageService(db_session)
        result = svc.resolve_research_metrics("nonexistent_strategy")
        assert result is None

    def test_resolve_research_metrics_by_run_id(self, db_session: Session):
        snap_id = _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        result = svc.resolve_research_metrics(_STRATEGY_ID, run_id=str(_RUN_UUID))
        assert result is not None
        assert result.metrics_snapshot_id == snap_id
        assert result.metric_lineage_type is MetricLineageType.RESEARCH
        assert result.run_id == str(_RUN_UUID)

    def test_resolve_research_metrics_fallback_via_json(self, db_session: Session):
        snap_id = _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        result = svc.resolve_research_metrics(_STRATEGY_ID)
        assert result is not None
        assert result.metrics_snapshot_id == snap_id

    def test_research_metrics_lineage_metadata(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        result = svc.resolve_research_metrics(_STRATEGY_ID)
        assert result is not None
        assert result.lineage.metric_lineage_type is MetricLineageType.RESEARCH
        assert result.lineage.calculation_version == "1.0"
        assert str(_RUN_UUID) in result.lineage.source_run_ids

    def test_research_metrics_immutable_from_live_perspective(self, db_session: Session):
        """Resolving research metrics twice returns identical values regardless of live data."""
        _make_metrics_summary(db_session)
        intent_id = _make_intent(db_session, _STRATEGY_ID)
        _make_fill(db_session, intent_id, side=Side.BUY, price=Decimal("150"), qty=Decimal("10"))
        _make_fill(db_session, intent_id, side=Side.SELL, price=Decimal("160"), qty=Decimal("10"))
        _make_equity_curve(db_session)

        svc = MetricLineageService(db_session)
        r1 = svc.resolve_research_metrics(_STRATEGY_ID)
        r2 = svc.resolve_research_metrics(_STRATEGY_ID)
        assert r1 is not None and r2 is not None
        assert r1.sharpe_ratio == r2.sharpe_ratio
        assert r1.total_return == r2.total_return
        assert r1.max_drawdown == r2.max_drawdown

    def test_resolve_live_metrics_lineage_type(self, db_session: Session):
        svc = MetricLineageService(db_session)
        live = svc.resolve_live_metrics(_STRATEGY_ID)
        assert live.metric_lineage_type is MetricLineageType.LIVE
        assert live.lineage.metric_lineage_type is MetricLineageType.LIVE

    def test_resolve_live_metrics_with_fills(self, db_session: Session):
        intent_id = _make_intent(db_session, _STRATEGY_ID)
        _make_fill(db_session, intent_id, side=Side.BUY, price=Decimal("100"), qty=Decimal("5"))
        _make_fill(db_session, intent_id, side=Side.SELL, price=Decimal("110"), qty=Decimal("5"))
        _make_equity_curve(db_session)

        svc = MetricLineageService(db_session)
        live = svc.resolve_live_metrics(_STRATEGY_ID)
        assert live.trade_count == 1
        assert live.winning_trade_count == 1

    def test_compute_blended_metrics_lineage_type(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        assert blended.metric_lineage_type is MetricLineageType.BLENDED
        assert blended.lineage.metric_lineage_type is MetricLineageType.BLENDED

    def test_blended_score_components_positive(self, db_session: Session):
        _make_metrics_summary(db_session, sharpe=1.5, total_return=0.20, trade_count=100)
        svc = MetricLineageService(db_session)
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        assert blended.blended_score >= 0.01
        assert blended.research_score > 0
        assert blended.live_score > 0

    def test_alpha_increases_with_live_history(self, db_session: Session):
        _make_metrics_summary(db_session)

        intent_id = _make_intent(db_session, _STRATEGY_ID)
        for i in range(60):
            _make_fill(
                db_session,
                intent_id,
                side=Side.BUY,
                price=Decimal("100"),
                qty=Decimal("1"),
                ts_offset_days=i + 1,
            )
            _make_fill(
                db_session,
                intent_id,
                side=Side.SELL,
                price=Decimal("105"),
                qty=Decimal("1"),
                ts_offset_days=i,
            )
        _make_equity_curve(db_session, days=120)

        svc = MetricLineageService(db_session)
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        assert blended.alpha > 0.50

    def test_new_strategy_alpha_near_minimum(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        assert blended.alpha == pytest.approx(0.10)

    def test_blended_dominated_by_research_when_alpha_low(self, db_session: Session):
        _make_metrics_summary(db_session, sharpe=2.0, total_return=0.30)
        svc = MetricLineageService(db_session)
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        expected = 0.10 * blended.live_score + 0.90 * blended.research_score
        assert blended.blended_score == pytest.approx(expected, rel=1e-4)

    def test_compute_and_persist_blended_stores_to_sor(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        blended = svc.compute_and_persist_blended(_STRATEGY_ID)
        row = db_session.get(BlendedMetricsSnapshot, blended.blended_snapshot_id)
        assert row is not None
        assert row.strategy_id == _STRATEGY_ID
        assert row.metric_lineage_type == "blended"
        assert row.alpha_weight == pytest.approx(blended.alpha)
        assert row.blended_score == pytest.approx(blended.blended_score)

    def test_get_latest_blended_returns_most_recent(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        svc.compute_and_persist_blended(_STRATEGY_ID)
        svc.compute_and_persist_blended(_STRATEGY_ID)
        result = svc.get_latest_blended(_STRATEGY_ID)
        assert result is not None
        assert result.strategy_id == _STRATEGY_ID

    def test_get_latest_blended_none_when_empty(self, db_session: Session):
        svc = MetricLineageService(db_session)
        assert svc.get_latest_blended("never_traded_strategy") is None

    def test_list_blended_history_respects_limit(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        for _ in range(5):
            svc.compute_and_persist_blended(_STRATEGY_ID)
        history = svc.list_blended_history(_STRATEGY_ID, limit=3)
        assert len(history) == 3

    def test_blended_metrics_reference_research_snapshot(self, db_session: Session):
        snap_id = _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        blended = svc.compute_and_persist_blended(_STRATEGY_ID)
        assert blended.research_metrics_snapshot_id == snap_id

    def test_live_metrics_snapshot_id_in_blended(self, db_session: Session):
        _make_metrics_summary(db_session)
        intent_id = _make_intent(db_session, _STRATEGY_ID)
        _make_fill(db_session, intent_id, side=Side.BUY, price=Decimal("100"), qty=Decimal("1"))
        svc = MetricLineageService(db_session)
        blended = svc.compute_and_persist_blended(_STRATEGY_ID)
        assert blended.live_metrics_snapshot_id is not None

    def test_blended_rebalance_run_id_stored(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session)
        blended = svc.compute_and_persist_blended(_STRATEGY_ID, rebalance_run_id="rebal-xyz")
        row = db_session.get(BlendedMetricsSnapshot, blended.blended_snapshot_id)
        assert row.rebalance_run_id == "rebal-xyz"

    def test_research_metrics_never_affected_by_live_fills(self, db_session: Session):
        """Adding losing fills must not change research metric values."""
        _make_metrics_summary(db_session, sharpe=1.8, total_return=0.25, trade_count=120)
        svc = MetricLineageService(db_session)
        before = svc.resolve_research_metrics(_STRATEGY_ID)
        assert before is not None

        intent_id = _make_intent(db_session, _STRATEGY_ID)
        _make_fill(db_session, intent_id, side=Side.BUY, price=Decimal("50"), qty=Decimal("10"))
        _make_fill(db_session, intent_id, side=Side.SELL, price=Decimal("40"), qty=Decimal("10"))

        after = svc.resolve_research_metrics(_STRATEGY_ID)
        assert after is not None
        assert before.sharpe_ratio == after.sharpe_ratio
        assert before.total_return == after.total_return
        assert before.trade_count == after.trade_count
        assert before.metrics_snapshot_id == after.metrics_snapshot_id

    def test_lineage_metadata_environment_propagates(self, db_session: Session):
        _make_metrics_summary(db_session)
        svc = MetricLineageService(db_session, environment="paper")
        blended = svc.compute_blended_metrics(_STRATEGY_ID)
        assert blended.lineage.environment == "paper"
        assert blended.lineage.source_strategy_ids == [_STRATEGY_ID]

    def test_sor_rows_have_lineage_type_on_new_live_snapshots(self, db_session: Session):
        svc_env = LivePerformanceMetricsService(db_session, environment="test")
        result = svc_env.compute_and_persist(_STRATEGY_ID)
        row = db_session.get(StrategyLivePerformanceSnapshot, result.snapshot_id)
        assert row is not None
        assert row.metric_lineage_type == "live"
        assert row.environment == "test"
        assert row.calculation_version == "1.0"

    def test_get_latest_live_returns_lineage_type(self, db_session: Session):
        svc = LivePerformanceMetricsService(db_session, environment="paper")
        svc.compute_and_persist(_STRATEGY_ID)
        result = svc.get_latest(_STRATEGY_ID)
        assert result is not None
        assert result.metric_lineage_type is MetricLineageType.LIVE


# ---------------------------------------------------------------------------
# Contract backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_metrics_summary_contract_new_fields_optional(self):
        from autonomous_trading_platform.contracts.runtime.metrics_summary import MetricsSummary

        now = datetime.now(UTC)
        ms = MetricsSummary(
            metrics_snapshot_id="snap-1",
            run_id="run-1",
            created_at=now,
            sharpe_ratio=1.0,
        )
        assert ms.metric_lineage_type is MetricLineageType.RESEARCH
        assert ms.environment is None
        assert ms.calculation_version is None

    def test_live_performance_metrics_contract_new_fields_optional(self):
        from autonomous_trading_platform.contracts.runtime.live_performance_metrics import (
            LivePerformanceMetrics,
        )

        now = datetime.now(UTC)
        lpm = LivePerformanceMetrics(
            snapshot_id="snap-1",
            strategy_id="strat-1",
            computed_at=now,
        )
        assert lpm.metric_lineage_type is MetricLineageType.LIVE
        assert lpm.environment is None

    def test_existing_services_still_construct_without_lineage(self, db_session: Session):
        svc = LivePerformanceMetricsService(db_session)
        result = svc.compute_for_strategy("some_strategy")
        assert result is not None
        assert result.metric_lineage_type is MetricLineageType.LIVE
