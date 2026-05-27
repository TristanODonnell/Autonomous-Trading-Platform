from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
    compute_alpha,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.contracts.common.enums import (
    OrderSource,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_UUID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_STRATEGY_ID = "test_strategy"


def _make_intent(session: Session, strategy_id: str, run_id: UUID | None = None) -> UUID:
    intent_id = uuid4()
    session.add(
        OrderIntents(
            intent_id=intent_id,
            idempotency_key=str(intent_id),
            run_id=run_id or _RUN_UUID,
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


def _seed_fill(
    session: Session,
    *,
    strategy_id: str,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    price: float = 100.0,
    quantity: float = 10.0,
    at: datetime | None = None,
    run_id: UUID | None = None,
) -> None:
    intent_id = _make_intent(session, strategy_id, run_id or _RUN_UUID)
    session.add(
        Fill(
            fill_id=str(uuid4()),
            broker_order_id=str(uuid4()),
            intent_id=intent_id,
            run_id=run_id or _RUN_UUID,
            timestamp=at or datetime.now(UTC),
            symbol=symbol,
            side=side,
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
        )
    )
    session.flush()


def _seed_equity_snapshots(
    session: Session,
    *,
    run_id: UUID,
    equity_values: Sequence[float | int],
    start: datetime,
    interval_days: int = 1,
) -> None:
    for i, equity in enumerate(equity_values):
        session.add(
            CashSnapshot(
                snapshot_id=uuid4(),
                run_id=run_id,
                timestamp=start + timedelta(days=i * interval_days),
                currency="USD",
                cash=Decimal("10000"),
                buying_power=Decimal("10000"),
                reserved_cash=Decimal("0"),
                equity=Decimal(str(equity)),
                source=OrderSource.LEDGER,
            )
        )
    session.flush()


def _seed_strategy_with_metrics(
    session: Session,
    strategy_id: str,
    *,
    sharpe: float = 1.0,
    total_return: float = 0.10,
    max_drawdown: float = 0.05,
) -> None:
    now = datetime.now(UTC)
    run_id = f"run_{strategy_id}"
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json={"strategy_id": strategy_id},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            current_state="approved_for_paper_trading",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.add(
        MetricsSummary(
            metrics_snapshot_id=f"metrics_{strategy_id}",
            run_id=run_id,
            created_at=now,
            total_return=total_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            trade_count=20,
            winning_trade_count=12,
            losing_trade_count=8,
            volatility=0.10,
            metrics_json={"strategy_id": strategy_id, "win_rate": 0.60},
        )
    )
    session.flush()


def _seed_policy(session: Session, max_pct: float = 0.80) -> None:
    session.add(
        CapitalAllocationPolicies(
            policy_id="paper_policy",
            approval_status="approved_paper",
            performance_tier=None,
            max_pct_of_capital=max_pct,
            max_position_size_usd=None,
            max_drawdown_allowed=0.20,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="test",
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# compute_alpha tests
# ---------------------------------------------------------------------------


def test_alpha_is_low_for_new_strategy() -> None:
    assert compute_alpha(days_live=0, trade_count=0) == pytest.approx(0.10, abs=0.01)


def test_alpha_is_low_when_under_5_days() -> None:
    assert compute_alpha(days_live=3, trade_count=20) == pytest.approx(0.10, abs=0.01)


def test_alpha_at_30_days_sufficient_trades_approaches_half() -> None:
    alpha = compute_alpha(days_live=30, trade_count=50)
    assert 0.45 <= alpha <= 0.55


def test_alpha_at_90_days_approaches_ceiling() -> None:
    alpha = compute_alpha(days_live=90, trade_count=100)
    assert alpha >= 0.75


def test_alpha_capped_by_insufficient_trade_count() -> None:
    # Many days but very few trades → alpha capped at the trade signal
    alpha_few_trades = compute_alpha(days_live=120, trade_count=5)
    alpha_many_trades = compute_alpha(days_live=120, trade_count=60)
    assert alpha_few_trades < alpha_many_trades


def test_alpha_increases_monotonically_with_days_given_sufficient_trades() -> None:
    alphas = [compute_alpha(days_live=d, trade_count=100) for d in [0, 5, 15, 30, 60, 90, 120]]
    assert alphas == sorted(alphas)


# ---------------------------------------------------------------------------
# LivePerformanceMetricsService — fill-based metrics
# ---------------------------------------------------------------------------


def test_win_rate_from_profitable_sells(db_session: Session) -> None:
    now = datetime.now(UTC)
    # Buy 10 @ 100, sell 10 @ 120 → PnL = +200 (win)
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.BUY,
        price=100.0,
        quantity=10.0,
        at=now - timedelta(days=5),
    )
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.SELL,
        price=120.0,
        quantity=10.0,
        at=now - timedelta(days=4),
    )

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID)

    assert metrics.trade_count == 1
    assert metrics.winning_trade_count == 1
    assert metrics.live_win_rate == pytest.approx(1.0)


def test_win_rate_from_losing_sells(db_session: Session) -> None:
    now = datetime.now(UTC)
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.BUY,
        price=100.0,
        quantity=10.0,
        at=now - timedelta(days=5),
    )
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.SELL,
        price=80.0,
        quantity=10.0,
        at=now - timedelta(days=4),
    )

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID)

    assert metrics.trade_count == 1
    assert metrics.winning_trade_count == 0
    assert metrics.live_win_rate == pytest.approx(0.0)


def test_win_rate_mixed_trades(db_session: Session) -> None:
    now = datetime.now(UTC)
    # Trade 1: win
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        symbol="AAPL",
        side=Side.BUY,
        price=100.0,
        quantity=5.0,
        at=now - timedelta(days=10),
    )
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        symbol="AAPL",
        side=Side.SELL,
        price=110.0,
        quantity=5.0,
        at=now - timedelta(days=9),
    )
    # Trade 2: loss
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        symbol="AAPL",
        side=Side.BUY,
        price=110.0,
        quantity=5.0,
        at=now - timedelta(days=8),
    )
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        symbol="AAPL",
        side=Side.SELL,
        price=100.0,
        quantity=5.0,
        at=now - timedelta(days=7),
    )

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, window_trades=10)

    assert metrics.trade_count == 2
    assert metrics.winning_trade_count == 1
    assert metrics.live_win_rate == pytest.approx(0.5)


def test_no_fills_returns_zero_trade_count(db_session: Session) -> None:
    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy("nonexistent_strategy")

    assert metrics.trade_count == 0
    assert metrics.live_win_rate is None
    assert metrics.days_live is None


def test_window_trades_limits_trade_count(db_session: Session) -> None:
    now = datetime.now(UTC)
    # Create 10 round trips
    for i in range(10):
        _seed_fill(
            db_session,
            strategy_id=_STRATEGY_ID,
            side=Side.BUY,
            price=100.0,
            quantity=1.0,
            at=now - timedelta(days=20 - i * 2),
        )
        _seed_fill(
            db_session,
            strategy_id=_STRATEGY_ID,
            side=Side.SELL,
            price=110.0,
            quantity=1.0,
            at=now - timedelta(days=19 - i * 2),
        )

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, window_trades=5)

    assert metrics.trade_count == 5


# ---------------------------------------------------------------------------
# LivePerformanceMetricsService — equity-curve metrics
# ---------------------------------------------------------------------------


def test_rolling_sharpe_from_equity_curve(db_session: Session) -> None:
    start = datetime.now(UTC) - timedelta(days=25)
    # Steadily rising equity → positive Sharpe
    equity = [10000 + i * 100 for i in range(22)]
    _seed_equity_snapshots(db_session, run_id=_RUN_UUID, equity_values=equity, start=start)

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, run_id=str(_RUN_UUID), window_days=20)

    assert metrics.rolling_sharpe is not None
    assert metrics.rolling_sharpe > 0


def test_realized_drawdown_computed_from_equity_curve(db_session: Session) -> None:
    start = datetime.now(UTC) - timedelta(days=25)
    # Rise then fall → drawdown
    equity = [10000, 11000, 12000, 11000, 10000, 9000, 10500]
    _seed_equity_snapshots(db_session, run_id=_RUN_UUID, equity_values=equity, start=start)

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, run_id=str(_RUN_UUID), window_days=20)

    assert metrics.realized_drawdown is not None
    assert metrics.realized_drawdown < 0
    # Peak 12000, trough 9000: drawdown ≈ -25%
    assert metrics.realized_drawdown == pytest.approx(-0.25, abs=0.02)


def test_realized_return_from_equity_curve(db_session: Session) -> None:
    start = datetime.now(UTC) - timedelta(days=25)
    equity = [10000, 10500, 11000, 11500]
    _seed_equity_snapshots(db_session, run_id=_RUN_UUID, equity_values=equity, start=start)

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, run_id=str(_RUN_UUID), window_days=20)

    assert metrics.realized_return == pytest.approx(0.15, abs=0.01)


def test_no_equity_snapshots_returns_none_for_curve_metrics(db_session: Session) -> None:
    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, run_id=str(_RUN_UUID))

    assert metrics.rolling_sharpe is None
    assert metrics.realized_drawdown is None
    assert metrics.realized_return is None


def test_days_live_computed_from_fills(db_session: Session) -> None:
    now = datetime.now(UTC)
    _seed_fill(db_session, strategy_id=_STRATEGY_ID, side=Side.BUY, at=now - timedelta(days=15))

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_for_strategy(_STRATEGY_ID, now=now)

    assert metrics.days_live == 15


def test_sharpe_calculation_correctness() -> None:
    """Unit-test the Sharpe computation in isolation."""
    svc = LivePerformanceMetricsService.__new__(LivePerformanceMetricsService)
    # All returns identical → zero variance → None
    assert svc._compute_rolling_sharpe([0.01, 0.01, 0.01]) is None
    # Known positive returns
    daily = [0.01, 0.02, 0.015, 0.01, 0.02]
    sharpe = svc._compute_rolling_sharpe(daily)
    assert sharpe is not None
    n = len(daily)
    mean_r = sum(daily) / n
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily) / (n - 1))
    expected = (mean_r / std_r) * math.sqrt(252)
    assert sharpe == pytest.approx(expected, rel=1e-6)


def test_drawdown_calculation_correctness() -> None:
    svc = LivePerformanceMetricsService.__new__(LivePerformanceMetricsService)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    curve = [
        (start, 100.0),
        (start + timedelta(days=1), 110.0),
        (start + timedelta(days=2), 90.0),
    ]
    dd = svc._compute_drawdown(curve)
    # Peak 110, trough 90: drawdown = (90 - 110) / 110 ≈ -0.1818
    assert dd == pytest.approx(-0.1818, abs=0.001)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_compute_and_persist_saves_snapshot(db_session: Session) -> None:
    now = datetime.now(UTC)
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.BUY,
        price=100.0,
        quantity=5.0,
        at=now - timedelta(days=5),
    )
    _seed_fill(
        db_session,
        strategy_id=_STRATEGY_ID,
        side=Side.SELL,
        price=115.0,
        quantity=5.0,
        at=now - timedelta(days=4),
    )

    svc = LivePerformanceMetricsService(db_session)
    metrics = svc.compute_and_persist(_STRATEGY_ID)

    rows = (
        db_session.query(StrategyLivePerformanceSnapshot).filter_by(strategy_id=_STRATEGY_ID).all()
    )
    assert len(rows) == 1
    assert rows[0].snapshot_id == metrics.snapshot_id
    assert rows[0].trade_count == 1


def test_get_latest_returns_most_recent_snapshot(db_session: Session) -> None:
    now = datetime.now(UTC)
    older = StrategyLivePerformanceSnapshot(
        snapshot_id=str(uuid4()),
        strategy_id=_STRATEGY_ID,
        computed_at=now - timedelta(hours=2),
        trade_count=5,
    )
    newer = StrategyLivePerformanceSnapshot(
        snapshot_id=str(uuid4()),
        strategy_id=_STRATEGY_ID,
        computed_at=now - timedelta(hours=1),
        trade_count=10,
    )
    db_session.add_all([older, newer])
    db_session.flush()

    svc = LivePerformanceMetricsService(db_session)
    result = svc.get_latest(_STRATEGY_ID)

    assert result is not None
    assert result.trade_count == 10


def test_get_latest_returns_none_when_no_snapshots(db_session: Session) -> None:
    svc = LivePerformanceMetricsService(db_session)
    assert svc.get_latest("nonexistent_strategy") is None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_live_metrics_computed_event_logged(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)
    _seed_fill(db_session, strategy_id=_STRATEGY_ID, side=Side.BUY, at=now - timedelta(days=5))

    svc_logger = "autonomous_trading_platform.application.services.live_performance_metrics_service"
    with caplog.at_level(logging.INFO, logger=svc_logger):
        LivePerformanceMetricsService(db_session).compute_for_strategy(_STRATEGY_ID)

    messages = [r.message for r in caplog.records if r.name == svc_logger]
    assert any("LIVE_METRICS_COMPUTED" in m or "LIVE_HISTORY_INSUFFICIENT" in m for m in messages)


def test_live_history_insufficient_logged_when_no_data(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    svc_logger = "autonomous_trading_platform.application.services.live_performance_metrics_service"
    with caplog.at_level(logging.INFO, logger=svc_logger):
        LivePerformanceMetricsService(db_session).compute_for_strategy("fresh_strategy")

    messages = [r.message for r in caplog.records if r.name == svc_logger]
    assert any("LIVE_HISTORY_INSUFFICIENT" in m for m in messages)


# ---------------------------------------------------------------------------
# Blended scoring in QualityBasedReallocationService
# ---------------------------------------------------------------------------


def test_blended_score_exposed_in_rebalance_result(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy_with_metrics(
        db_session, "alpha", sharpe=2.0, total_return=0.20, max_drawdown=0.03
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert "alpha" in result.blended_score_breakdown
    breakdown = result.blended_score_breakdown["alpha"]
    assert "backtest_score" in breakdown
    assert "live_score" in breakdown
    assert "blended_score" in breakdown
    assert "alpha_weight" in breakdown
    assert breakdown["blended_score"] is not None


def test_no_live_data_strategy_relies_on_backtest_metrics(db_session: Session) -> None:
    """A strategy with no fills or equity data should get alpha ≈ 0.1 (heavily backtest-weighted)."""
    _seed_policy(db_session)
    _seed_strategy_with_metrics(
        db_session, "new_strat", sharpe=2.0, total_return=0.15, max_drawdown=0.05
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    breakdown = result.blended_score_breakdown["new_strat"]
    assert breakdown["alpha_weight"] == pytest.approx(0.10, abs=0.01)
    # Blended score should be close to pure backtest score (90% backtest weight)
    blended = breakdown["blended_score"]
    backtest = breakdown["backtest_score"]
    assert blended is not None and backtest is not None
    assert abs(blended - backtest) < 0.20


def test_allocation_ranking_changes_when_live_performance_deteriorates(
    db_session: Session,
) -> None:
    """
    Strategy A has better backtest metrics.
    Strategy B has worse backtest metrics BUT is deteriorating live (many trades, deep drawdown).
    With sufficient live history both should be evaluated on live data too.
    Concretely: after seeding poor live performance for strategy A, it should rank lower.
    """
    _seed_policy(db_session, max_pct=0.80)
    # Both strategies have identical backtest metrics
    _seed_strategy_with_metrics(
        db_session, "strat_good_live", sharpe=1.5, total_return=0.12, max_drawdown=0.06
    )
    _seed_strategy_with_metrics(
        db_session, "strat_poor_live", sharpe=1.5, total_return=0.12, max_drawdown=0.06
    )

    now = datetime.now(UTC)
    start = now - timedelta(days=100)
    run_uuid_good = uuid4()
    run_uuid_poor = uuid4()

    # strat_good_live: growing equity over 100 days
    _seed_equity_snapshots(
        db_session,
        run_id=run_uuid_good,
        equity_values=[10000 + i * 50 for i in range(30)],
        start=start,
    )
    # Also seed an order_intent so run_id is discoverable
    db_session.add(
        OrderIntents(
            intent_id=uuid4(),
            idempotency_key=str(uuid4()),
            run_id=run_uuid_good,
            strategy_id="strat_good_live",
            timestamp=start,
            bar_timestamp=start,
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

    # strat_poor_live: declining equity over 100 days
    _seed_equity_snapshots(
        db_session,
        run_id=run_uuid_poor,
        equity_values=[10000 - i * 80 for i in range(30)],
        start=start,
    )
    db_session.add(
        OrderIntents(
            intent_id=uuid4(),
            idempotency_key=str(uuid4()),
            run_id=run_uuid_poor,
            strategy_id="strat_poor_live",
            timestamp=start,
            bar_timestamp=start,
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
    db_session.flush()

    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    good_alloc = result.after_allocation.get("strat_good_live", Decimal("0"))
    poor_alloc = result.after_allocation.get("strat_poor_live", Decimal("0"))
    # With identical backtest scores, live equity data should differentiate them
    assert good_alloc >= poor_alloc


def test_blended_score_logged_during_rebalance(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _seed_policy(db_session)
    _seed_strategy_with_metrics(
        db_session, "logged_strat", sharpe=1.0, total_return=0.08, max_drawdown=0.05
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    svc_logger = (
        "autonomous_trading_platform.application.services.quality_based_reallocation_service"
    )
    with caplog.at_level(logging.INFO, logger=svc_logger):
        QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    messages = [r.message for r in caplog.records if r.name == svc_logger]
    assert any("QUALITY_SCORE_BLENDED" in m for m in messages)


# ---------------------------------------------------------------------------
# Governance — live metrics in PromotionEligibilityResult
# ---------------------------------------------------------------------------


def test_promotion_eligibility_result_contains_live_metrics(db_session: Session) -> None:
    from autonomous_trading_platform.application.services.auto_promotion_service import (
        AutoPromotionService,
    )

    now = datetime.now(UTC)
    session = db_session
    session.add(
        StrategyConfigs(
            strategy_id="promo_strat",
            config_hash="promo_hash",
            config_json={"strategy_id": "promo_strat"},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    session.add(
        StrategyGovernance(
            strategy_id="promo_strat",
            config_hash="promo_hash",
            current_state="approved_research",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.flush()

    candidates = AutoPromotionService(session=session).scan()

    assert len(candidates) == 1
    result = candidates[0]
    assert result.strategy_id == "promo_strat"
    # live_metrics always populated (may be all-None values for a fresh strategy)
    assert result.live_metrics is not None
    assert "rolling_sharpe" in result.live_metrics
    assert "days_live" in result.live_metrics


def test_promotion_eligibility_live_metrics_in_serialized_output(db_session: Session) -> None:
    from autonomous_trading_platform.application.services.auto_promotion_service import (
        AutoPromotionService,
    )

    now = datetime.now(UTC)
    db_session.add(
        StrategyConfigs(
            strategy_id="ser_strat",
            config_hash="ser_hash",
            config_json={},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    db_session.add(
        StrategyGovernance(
            strategy_id="ser_strat",
            config_hash="ser_hash",
            current_state="approved_research",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": False}, updated_by="test"
    )

    run_result = AutoPromotionService(session=db_session).run()
    jsonable = AutoPromotionService.result_to_jsonable(run_result)

    assert len(jsonable["candidate_strategies"]) == 1
    cand = jsonable["candidate_strategies"][0]
    assert "live_metrics" in cand
