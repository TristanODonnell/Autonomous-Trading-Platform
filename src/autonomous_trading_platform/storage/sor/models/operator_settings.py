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
