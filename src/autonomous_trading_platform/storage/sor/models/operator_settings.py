# storage/sor/models/operator_settings.py

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.storage.sor.models.base import Base


class OperatorSettingsRow(Base):
    __tablename__ = "operator_settings"

    settings_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    risk_tolerance: Mapped[str] = mapped_column(String(16), nullable=False)
    max_drawdown_limit: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    max_strategy_drawdown: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    rebalance_frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    auto_promote_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    auto_rebalance_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Deprecated compatibility fields. PromotionRules is the source of truth for
    # governance eligibility thresholds.
    min_sharpe_for_promotion: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    min_paper_trading_period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_demote_on_breach: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notify_drawdown_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notify_strategy_promotion_events: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notify_strategy_demotion_events: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    notify_allocation_rebalance_events: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    notify_pipeline_failures: Mapped[bool] = mapped_column(Boolean, nullable=False)
    per_strategy_cap: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    target_portfolio_volatility: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    slippage_model: Mapped[str | None] = mapped_column(String(32), nullable=True, default="fixed")
    transaction_cost_model: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="per_share"
    )
    max_total_strategy_allocation_pct: Mapped[float | None] = mapped_column(
        Numeric(6, 4), nullable=True, default=1.0
    )
    max_portfolio_symbol_exposure_usd: Mapped[float | None] = mapped_column(
        Numeric(16, 2), nullable=True, default=None
    )
    max_portfolio_symbol_pct: Mapped[float | None] = mapped_column(
        Numeric(6, 4), nullable=True, default=None
    )
    min_rebalance_interval_hours: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False, default=24.0
    )
    min_allocation_change_pct: Mapped[float] = mapped_column(
        Numeric(6, 4), nullable=False, default=0.01
    )
    turnover_penalty_weight: Mapped[float | None] = mapped_column(
        Numeric(8, 4), nullable=True, default=None
    )

    # Active portfolio set (portfolio rotation step 1). Off = legacy single-strategy cycle.
    portfolio_mode_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_active_strategies: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_active_strategies: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    # On-deck shadow tier (portfolio rotation step 2). 0 disables on-deck.
    max_on_deck_strategies: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    # Bench management (portfolio rotation step 3). Off = every candidate may go on-deck.
    bench_management_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_bench_strategies: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    # Daily re-sim return correlation at or above which two strategies are redundant.
    bench_correlation_threshold: Mapped[float] = mapped_column(
        Numeric(6, 4), nullable=False, default=0.85
    )
    bench_resim_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=63)
    # Re-sim quality score below which a review counts as a strike (1.0 = flat).
    bench_score_floor: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=1.0)
    bench_floor_strikes: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    bench_max_idle_days: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    # Portfolio review (portfolio rotation step 4): off / advisory / auto.
    portfolio_review_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    # Relative score edge a challenger needs over an incumbent (after turnover cost).
    review_swap_margin: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0.10)
    # Consecutive weekly reviews the edge must hold before a swap.
    review_swap_consecutive: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    review_min_tenure_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    review_max_swaps_per_review: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Days between swap-eligible (monthly) reviews.
    review_swap_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=28)
    # Round-trip cost of moving an incumbent's sleeve, charged against the challenger's edge.
    review_turnover_cost_bps: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False, default=20
    )
    # Shadow record a candidate needs before the review may promote it to paper.
    review_min_shadow_days: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    review_min_shadow_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    # Absolute score floor for taking an open seat / keeping one above min (1.0 = flat).
    review_score_floor: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=1.0)
    review_on_deck_min_tenure_days: Mapped[int] = mapped_column(Integer, nullable=False, default=21)

    # Portfolio drawdown governance (FINDING-16)
    portfolio_max_drawdown_pct: Mapped[float | None] = mapped_column(
        Numeric(6, 4), nullable=True, default=0.15
    )
    portfolio_drawdown_action: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="pause_new_trading"
    )
    portfolio_drawdown_recovery_mode: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="manual_resume_required"
    )

    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
